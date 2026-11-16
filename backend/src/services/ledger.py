"""计划账本：按上海日历把每个应发时点落成唯一归属的一行。

设计要点
- SubscriptionSnapshot 以半开区间 [effective_from, effective_to) 记录每次改站/改收件箱/
  改时刻/暂停恢复的划界；应发时点落在哪个区间，就唯一归属哪个快照。
- DeliveryLedger 对 (subscription_id, due_at) 加唯一约束：同一应发时点不会有两行，
  并发扫描与计划修改的归属由此确定。
- 暂停区间的应发时点落 skipped_pause，不生成回执，恢复后也不补；启用区间的欠报落
  pending，由调度逐个追补，空日也生成回执（receipt.state='empty'）。
- 每个应发时点一个事务：回执与账本推进同提交；中断后重试用 (subscription_id,report_day)
  唯一约束对账，已入箱的只补账本状态，绝不重复入箱，也不跳过欠报。
"""
import json,logging
from datetime import timedelta
from pathlib import Path
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.models.entities import Subscription,SubscriptionSnapshot,DeliveryLedger,Receipt,Audit
from src.services.digest import calculate
from src.utils.time import (aware,utc_iso,sh_date,occurrence_utc,report_day_for_due,next_due)

LEDGER_PENDING='pending'
LEDGER_DELIVERED='delivered'
LEDGER_EMPTY='empty'
LEDGER_SKIPPED_PAUSE='skipped_pause'
LEDGER_STATES=(LEDGER_PENDING,LEDGER_DELIVERED,LEDGER_EMPTY,LEDGER_SKIPPED_PAUSE)
DEMARCATING_FIELDS=('station','daily_at','recipient','enabled')

def is_postgres(s):
 return s.bind.dialect.name=='postgresql'

def lock_subscription(s,sub_id):
 """行锁确定唯一归属；SQLite（本地测试）不支持FOR UPDATE，方言层自动忽略。"""
 q=select(Subscription).where(Subscription.id==sub_id)
 if is_postgres(s):q=q.with_for_update(skip_locked=True)
 return s.scalars(q).first()

def open_snapshot(s,sub,now,reason):
 previous=s.scalars(select(SubscriptionSnapshot).where(
  SubscriptionSnapshot.subscription_id==sub.id,
  SubscriptionSnapshot.effective_to.is_(None)).order_by(SubscriptionSnapshot.id.desc())).first()
 if previous:previous.effective_to=utc_iso(now)
 snap=SubscriptionSnapshot(subscription_id=sub.id,effective_from=utc_iso(now),effective_to=None,
  station=sub.station,daily_at=sub.daily_at,recipient=sub.recipient,enabled=bool(sub.enabled),change_reason=reason)
 s.add(snap);s.flush()
 return snap

def snapshot_at(s,sub_id,due):
 """应发时点的唯一归属快照：半开区间包含判定，旧订阅无快照时回退最早快照。"""
 iso=utc_iso(due)
 snap=s.scalars(select(SubscriptionSnapshot).where(
  SubscriptionSnapshot.subscription_id==sub_id,
  SubscriptionSnapshot.effective_from<=iso,
  (SubscriptionSnapshot.effective_to.is_(None))|(SubscriptionSnapshot.effective_to>iso)
 ).order_by(SubscriptionSnapshot.id.desc())).first()
 if snap:return snap
 return s.scalars(select(SubscriptionSnapshot).where(
  SubscriptionSnapshot.subscription_id==sub_id).order_by(SubscriptionSnapshot.id.asc())).first()

def materialize(s,sub,now):
 """沿上海日历补全截至 now 的全部应发时点，逐快照用当时的daily_at展开。"""
 created=[]
 snaps=s.scalars(select(SubscriptionSnapshot).where(
  SubscriptionSnapshot.subscription_id==sub.id).order_by(SubscriptionSnapshot.id.asc())).all()
 for snap in snaps:
  eff_from=aware(snap.effective_from);eff_to=aware(snap.effective_to) if snap.effective_to else None
  # 起点回退一天：快照可能在某日白天生效，而当天HH:MM应发时点在生效之前，由窗口判断排除。
  day=sh_date(eff_from)-timedelta(days=1);end_day=sh_date(now)
  while day<=end_day:
   due=occurrence_utc(snap.daily_at,day)
   in_window=eff_from<=due and due<=now
   if eff_to is not None:in_window=in_window and due<eff_to
   if in_window:
    iso=utc_iso(due)
    exists=s.scalars(select(DeliveryLedger).where(
     DeliveryLedger.subscription_id==sub.id,DeliveryLedger.due_at==iso)).first()
    if not exists:
     state=LEDGER_PENDING if snap.enabled else LEDGER_SKIPPED_PAUSE
     basis='应发时点 %s 归属快照#%s（%s 生效，%s，港站=%s，收件箱=%s，时刻=%s）；%s'%(
      iso,snap.id,snap.effective_from,'启用' if snap.enabled else '暂停',
      snap.station,snap.recipient,snap.daily_at,
      '暂停区间不补报' if not snap.enabled else '历史欠报沿用当时计划追补')
     row=DeliveryLedger(subscription_id=sub.id,snapshot_id=snap.id,due_at=iso,
      report_day=report_day_for_due(due),station=snap.station,recipient=snap.recipient,
      state=state,basis=basis)
     # SAVEPOINT兜底并发：唯一约束冲突只回滚本行，不波及调用方事务
     try:
      sp=s.begin_nested();s.add(row);sp.commit()
     except IntegrityError:
      sp.rollback()
      continue
     created.append(row)
   day+=timedelta(days=1)
 return created

def _provenance(row,payload,now):
 return dict(ledger_id=row.id,snapshot_id=row.snapshot_id,due_at=row.due_at,
  report_day=row.report_day,station_at_due=row.station,recipient_at_due=row.recipient,
  frozen_at=utc_iso(now),basis=row.basis,source_count=payload['source_count'],
  source_codes=payload['source_codes'],source_checksum=payload['checksum'])

def process_pending(s,sub,now,rows_loader,artifact_dir=None,crash_hook=None):
 """按应发时点从早到晚逐个追补；每个时点独立事务，崩溃恢复不重复不跳过。"""
 summary=dict(delivered=[],empty=[],reconciled=[],skipped=0,processed=0)
 now_iso=utc_iso(now)
 pending=s.scalars(select(DeliveryLedger).where(
  DeliveryLedger.subscription_id==sub.id,
  DeliveryLedger.state==LEDGER_PENDING,
  DeliveryLedger.due_at<=now_iso).order_by(DeliveryLedger.due_at.asc())).all()
 for row in pending:
  snap=snapshot_at(s,sub.id,aware(row.due_at))
  if snap and snap.id!=row.snapshot_id:
   row.snapshot_id=snap.id;row.station=snap.station;row.recipient=snap.recipient
   if not snap.enabled:
    row.state=LEDGER_SKIPPED_PAUSE;row.attempts+=1;row.last_attempt_at=now_iso
    s.commit();summary['skipped']+=1;continue
  row.attempts+=1;row.last_attempt_at=now_iso
  # 崩溃恢复对账：回执已入箱而账本未推进时，只补账本，绝不重复入箱。
  existing=s.query(Receipt).filter_by(subscription_id=sub.id,report_day=row.report_day).first()
  if existing:
   row.receipt_id=existing.id
   row.state=LEDGER_EMPTY if (existing.payload or {}).get('empty_day') else LEDGER_DELIVERED
   s.commit();summary['reconciled'].append(row.id);summary['processed']+=1
   continue
  payload=calculate(rows_loader(s),row.report_day,row.station,provenance=None)
  payload['provenance']=_provenance(row,payload,now)
  is_empty=payload['empty_day']
  receipt=Receipt(subscription_id=sub.id,report_day=row.report_day,recipient=row.recipient,
   created_at=now_iso,state='empty' if is_empty else 'delivered',payload=payload)
  s.add(receipt);s.flush()
  row.receipt_id=receipt.id
  row.state=LEDGER_EMPTY if is_empty else LEDGER_DELIVERED
  sub.last_run_at=now_iso
  s.add(Audit(actor='scheduler',
   action=('空日留痕追补' if is_empty else '追补到点分发')+' 订阅 %s / %s / 账本#%s'%(sub.id,row.report_day,row.id),
   created=now_iso))
  if crash_hook=='after_flush_before_commit':raise RuntimeError('演练：入箱后推进提交前中断')
  s.commit()
  summary['processed']+=1
  summary['empty' if is_empty else 'delivered'].append(receipt.id)
  if artifact_dir:
   try:
    Path(artifact_dir).mkdir(parents=True,exist_ok=True)
    Path(artifact_dir,str(receipt.id)+'.json').write_text(json.dumps(receipt.payload,ensure_ascii=False,indent=2))
   except Exception:logging.exception('DB inbox delivered; optional artifact write failed')
 sub.next_run_at=utc_iso(next_due(sub.daily_at,now))
 s.commit()
 return summary

def ledger_rows(s,sub_id=None):
 q=select(DeliveryLedger)
 if sub_id is not None:q=q.where(DeliveryLedger.subscription_id==sub_id)
 return s.scalars(q.order_by(DeliveryLedger.subscription_id.asc(),DeliveryLedger.due_at.asc())).all()

def ledger_summary(s):
 out=[]
 for sub in s.scalars(select(Subscription).order_by(Subscription.id.asc())).all():
  rows=s.scalars(select(DeliveryLedger).where(DeliveryLedger.subscription_id==sub.id)).all()
  counts={st:sum(1 for r in rows if r.state==st) for st in LEDGER_STATES}
  out.append(dict(subscription_id=sub.id,name=sub.name,enabled=bool(sub.enabled),
   total=len(rows),due_count=sum(1 for r in rows if r.state in (LEDGER_DELIVERED,LEDGER_EMPTY)),
   delivered=counts[LEDGER_DELIVERED],empty=counts[LEDGER_EMPTY],
   pending=counts[LEDGER_PENDING],skipped_pause=counts[LEDGER_SKIPPED_PAUSE]))
 return out
