"""计划账本与追补调度演练。

覆盖：首次到点沿用现规则、跨午夜停机追补、按当时港站筛选与延误重算、空日留回执、
改站/改收件箱/改时刻/暂停恢复的生效划界、暂停期间不补、逐日应发/已发/欠报依据、
并发归属唯一、入箱后推进前崩溃的重试幂等、旧回执可读。
"""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import pytest
from sqlalchemy import select
from src.schemas.business import ArrivalInput,SubscriptionInput
from src.services.repository import save_arrival,save_subscription
from src.services.scheduler import run_due
from src.services.ledger import (materialize,process_pending,open_snapshot,snapshot_at,
 ledger_summary,LEDGER_PENDING,LEDGER_DELIVERED,LEDGER_EMPTY,LEDGER_SKIPPED_PAUSE)
from src.models.entities import Subscription,SubscriptionSnapshot,DeliveryLedger,Receipt,Arrival
from src.services.repository import arrival_rows
from src.utils.time import SH,aware,utc_iso

def sh(y,m,d,h=0,mi=0):return datetime(y,m,d,h,mi,tzinfo=SH)
def iso(dt):return utc_iso(dt)

def make_sub(session,**kw):
 data=dict(name='北站08:30值班晨报',station='北站',daily_at='08:30',
  recipient='北站值班室',enabled=True,first_due_at=None)
 data.update(kw)
 first=aware(data['first_due_at']) if data.get('first_due_at') else sh(2026,11,14,8,30)
 return save_subscription(session,SubscriptionInput(**data),now=first-timedelta(minutes=5))

def edit_sub(session,i,**kw):
 base=dict(name=session.get(Subscription,i).name,station='北站',daily_at='08:30',
  recipient='北站值班室',enabled=True,first_due_at=None)
 base.update(kw)
 change_at=aware(base['first_due_at']) if base.get('first_due_at') else sh(2026,11,14,9,0)
 return save_subscription(session,SubscriptionInput(**base),i,now=change_at)

def add_arrival(session,code,station,planned,actual=None,note='演练'):
 return save_arrival(session,ArrivalInput(code=code,station=station,
  planned_at=iso(planned),actual_at=iso(actual) if actual else None,note=note))

def ledger_of(session,sub_id):
 return session.scalars(select(DeliveryLedger).where(
  DeliveryLedger.subscription_id==sub_id).order_by(DeliveryLedger.due_at)).all()

# ---------- 基础：首次到点沿用现规则，旧回执口径可读 ----------
def test_first_due_follows_existing_rule_and_empty_day_receipt(session):
 t0=sh(2026,11,14,8,30)
 sub=make_sub(session,first_due_at=iso(t0))
 assert aware(sub.next_run_at)==t0
 run_due(session,t0)
 rows=ledger_of(session,sub.id)
 assert [r.report_day for r in rows]==['2026-11-13']
 assert rows[0].state==LEDGER_EMPTY
 receipts=session.query(Receipt).filter_by(subscription_id=sub.id).all()
 assert len(receipts)==1 and receipts[0].state=='empty'
 # 空日回执仍带完整口径与来源字段
 p=receipts[0].payload
 assert p['empty_day'] is True and p['source_count']==0 and p['checksum']
 assert p['provenance']['station_at_due']=='北站'
 # 游标推进到次日同时刻
 assert aware(session.get(Subscription,sub.id).next_run_at)==sh(2026,11,15,8,30)

def test_legacy_receipt_without_provenance_stays_readable(session):
 # 模拟旧版回执：无 empty_day/provenance 字段，前端与导出仍可读
 old=Receipt(subscription_id=None,report_day='2026-11-10',recipient='旧收件箱',
  created_at=iso(sh(2026,11,11,8,30)),state='delivered',
  payload=dict(report_day='2026-11-10',scheduled=3,completed=2,pending=1,p50=4.0,
   p95=None,late_rate=0.5,checksum='abc',rows=[],delays=[],version='planned-day-linear-quantile-v1'))
 session.add(old);session.commit()
 got=session.query(Receipt).order_by(Receipt.id).first()
 assert got.payload['scheduled']==3 and 'provenance' not in got.payload

# ---------- 跨午夜停机：恢复后逐日追补，不只是昨日 ----------
def test_multi_day_outage_catchup_day_by_day_with_frozen_sources(session,tmp_path):
 add_arrival(session,'LG-1','北站',sh(2026,11,13,23,55),sh(2026,11,14,0,5))
 add_arrival(session,'LG-2','北站',sh(2026,11,13,9,0),sh(2026,11,13,9,15))
 add_arrival(session,'LG-3','北站',sh(2026,11,14,10,0),sh(2026,11,14,10,20))
 add_arrival(session,'LG-4','南站',sh(2026,11,13,9,0),sh(2026,11,13,9,0))
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 # 14日08:30的一次正常到点
 run_due(session,sh(2026,11,14,8,30))
 r1=session.query(Receipt).filter_by(subscription_id=sub.id,report_day='2026-11-13').one()
 assert r1.payload['scheduled']==2 and r1.payload['completed']==2
 # 跨午夜班次归计划日，延误10分钟；LG-4属于南站被港站筛选排除
 codes=sorted(x['code'] for x in r1.payload['rows'])
 assert codes==['LG-1','LG-2']
 assert sorted(r1.payload['provenance']['source_codes'])==codes
 # 停机：15、16日的到点全部错过，17日09:00才恢复
 run_due(session,sh(2026,11,17,9,0))
 rows=ledger_of(session,sub.id)
 assert [r.report_day for r in rows]==['2026-11-13','2026-11-14','2026-11-15','2026-11-16']
 states=[r.state for r in rows]
 assert states==[LEDGER_DELIVERED,LEDGER_DELIVERED,LEDGER_EMPTY,LEDGER_EMPTY]
 # 每个报告日恰好一份回执，无重复
 for day in ('2026-11-13','2026-11-14','2026-11-15','2026-11-16'):
  assert session.query(Receipt).filter_by(subscription_id=sub.id,report_day=day).count()==1
 # 15日晨报（报告日14日）只含北站的LG-3，按当时北站筛选并冻结
 r2=session.query(Receipt).filter_by(subscription_id=sub.id,report_day='2026-11-14').one()
 assert [x['code'] for x in r2.payload['rows']]==['LG-3']
 assert r2.payload['provenance']['due_at']==iso(sh(2026,11,15,8,30))
 # 全部入箱后无欠报
 summ=[x for x in ledger_summary(session) if x['subscription_id']==sub.id][0]
 assert summ['pending']==0 and summ['delivered']==2 and summ['empty']==2 and summ['total']==4
 # 产物卷每个回执一份
 artifacts=list((tmp_path/'artifacts').glob('*.json'))
 assert len(artifacts)==4

def test_late_arrival_after_delivery_does_not_rewrite_frozen_receipt(session):
 add_arrival(session,'LG-1','北站',sh(2026,11,13,9,0),None)
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 run_due(session,sh(2026,11,14,8,30))
 receipt=session.query(Receipt).filter_by(subscription_id=sub.id,report_day='2026-11-13').one()
 assert receipt.payload['completed']==0 and receipt.payload['pending']==1
 checksum=receipt.payload['checksum']
 # 之后补录实际到发（同编号修订）：已冻结回执不被重算
 existing=session.query(Arrival).filter_by(code='LG-1').one()
 save_arrival(session,ArrivalInput(code='LG-1',station='北站',
  planned_at=iso(sh(2026,11,13,9,0)),actual_at=iso(sh(2026,11,13,9,30)),note='补录'),existing.id)
 run_due(session,sh(2026,11,15,8,30))
 again=session.query(Receipt).filter_by(subscription_id=sub.id,report_day='2026-11-13').one()
 assert again.payload['checksum']==checksum and again.payload['completed']==0

# ---------- 改站/收件箱/时刻：从生效时点划界，历史欠报沿用当时计划 ----------
def test_station_change_demarcation_uses_plan_at_due_time(session):
 add_arrival(session,'N1','北站',sh(2026,11,13,9,0),sh(2026,11,13,9,5))
 add_arrival(session,'N2','北站',sh(2026,11,14,9,0),sh(2026,11,14,9,8))
 add_arrival(session,'S1','南站',sh(2026,11,13,9,0),sh(2026,11,13,9,25))
 add_arrival(session,'S2','南站',sh(2026,11,15,9,0),sh(2026,11,15,9,25))
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 # 15日10:00改南站：15日08:30的欠报仍归属北站旧计划，16日起按南站
 edit_sub(session,sub.id,name=sub.name,station='南站',
  first_due_at=iso(sh(2026,11,15,10,0)))
 run_due(session,sh(2026,11,17,9,0))
 days={r.report_day:r for r in ledger_of(session,sub.id)}
 assert days['2026-11-13'].station=='北站'
 assert days['2026-11-14'].station=='北站'   # 应发15日08:30，改站前
 assert days['2026-11-15'].station=='南站'   # 应发16日08:30，改站后
 r_old=session.query(Receipt).filter_by(subscription_id=sub.id,report_day='2026-11-14').one()
 r_new=session.query(Receipt).filter_by(subscription_id=sub.id,report_day='2026-11-15').one()
 assert [x['code'] for x in r_old.payload['rows']]==['N2']
 assert [x['code'] for x in r_new.payload['rows']]==['S2']
 assert r_old.recipient=='北站值班室'
 # 快照划界可追溯
 snaps=session.scalars(select(SubscriptionSnapshot).where(
  SubscriptionSnapshot.subscription_id==sub.id).order_by(SubscriptionSnapshot.id)).all()
 assert len(snaps)==2 and snaps[0].station=='北站' and snaps[1].station=='南站'
 assert aware(snaps[0].effective_to)==sh(2026,11,15,10,0)

def test_recipient_change_only_affects_new_due_points(session):
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 edit_sub(session,sub.id,name=sub.name,recipient='联运总值班室',
  first_due_at=iso(sh(2026,11,15,12,0)))
 run_due(session,sh(2026,11,16,9,0))
 rows=ledger_of(session,sub.id)
 recipients={r.report_day:r.recipient for r in rows}
 assert recipients['2026-11-13']=='北站值班室'
 assert recipients['2026-11-14']=='北站值班室'
 assert recipients['2026-11-15']=='联运总值班室'
 receipts={r.report_day:r for r in session.query(Receipt).filter_by(subscription_id=sub.id)}
 assert receipts['2026-11-15'].recipient=='联运总值班室'

def test_daily_at_change_regrids_calendar_from_effective_point(session):
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 # 15日改成07:00并指定生效时刻为15日06:00
 edit_sub(session,sub.id,name=sub.name,daily_at='07:00',
  first_due_at=iso(sh(2026,11,15,6,0)))
 run_due(session,sh(2026,11,16,9,0))
 rows=ledger_of(session,sub.id)
 dues=[r.due_at for r in rows]
 assert iso(sh(2026,11,14,8,30)) in dues and iso(sh(2026,11,15,7,0)) in dues
 assert iso(sh(2026,11,16,7,0)) in dues and iso(sh(2026,11,15,8,30)) not in dues
 # 没有任何一个应发时点出现两行
 assert len(dues)==len(set(dues))

# ---------- 暂停/恢复：暂停期间不补，恢复后只补恢复之后 ----------
def test_pause_window_never_delivered_even_after_resume(session):
 add_arrival(session,'LG-1','北站',sh(2026,11,13,9,0),sh(2026,11,13,9,10))
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 run_due(session,sh(2026,11,14,9,0))
 # 14日18:00暂停
 edit_sub(session,sub.id,name=sub.name,enabled=False,
  first_due_at=iso(sh(2026,11,14,18,0)))
 # 17日10:00恢复
 edit_sub(session,sub.id,name=sub.name,enabled=True,
  first_due_at=iso(sh(2026,11,17,10,0)))
 run_due(session,sh(2026,11,18,9,0))
 rows=ledger_of(session,sub.id)
 by_day={r.report_day:r.state for r in rows}
 assert by_day['2026-11-13']==LEDGER_DELIVERED      # 暂停前已发
 assert by_day['2026-11-14']==LEDGER_SKIPPED_PAUSE # 应发15日08:30，暂停中
 assert by_day['2026-11-15']==LEDGER_SKIPPED_PAUSE
 assert by_day['2026-11-16']==LEDGER_SKIPPED_PAUSE
 assert by_day['2026-11-17'] in (LEDGER_EMPTY,LEDGER_DELIVERED) # 应发18日08:30，恢复后
 # 暂停期间没有任何回执
 for day in ('2026-11-14','2026-11-15','2026-11-16'):
  assert session.query(Receipt).filter_by(subscription_id=sub.id,report_day=day).count()==0
 summ=[x for x in ledger_summary(session) if x['subscription_id']==sub.id][0]
 assert summ['skipped_pause']==3 and summ['pending']==0

def test_paused_subscription_scanned_but_not_advanced(session):
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 edit_sub(session,sub.id,name=sub.name,enabled=False,first_due_at=iso(sh(2026,11,14,9,0)))
 before=session.get(Subscription,sub.id).next_run_at
 run_due(session,sh(2026,11,20,9,0))
 after=session.get(Subscription,sub.id)
 assert after.enabled is False and after.next_run_at==before
 rows=ledger_of(session,sub.id)
 # 暂停生效前的应发时点仍是欠报（未被扫描追补），暂停生效后全部 skipped_pause
 pause=sh(2026,11,14,9,0)
 assert all((r.state==LEDGER_SKIPPED_PAUSE)==(aware(r.due_at)>=pause) for r in rows)
 assert any(r.state==LEDGER_SKIPPED_PAUSE for r in rows)
 # 暂停订阅不产生任何回执
 assert session.query(Receipt).filter_by(subscription_id=sub.id).count()==0

# ---------- 崩溃恢复：入箱后推进前崩溃，重试不重复不跳过 ----------
def test_crash_after_inbox_before_ledger_advance_is_idempotent(session):
 add_arrival(session,'LG-1','北站',sh(2026,11,13,9,0),sh(2026,11,13,9,10))
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 materialize(session,sub,sh(2026,11,14,8,30))
 session.commit()
 with pytest.raises(RuntimeError):
  process_pending(session,sub,sh(2026,11,14,8,30),arrival_rows,
   artifact_dir='/tmp/port-digest-test-artifacts',crash_hook='after_flush_before_commit')
 # worker进程随异常退出，未提交事务回滚：回执与账本推进都不落库
 session.rollback()
 assert session.query(Receipt).filter_by(subscription_id=sub.id).count()==0
 row=ledger_of(session,sub.id)[0]
 assert row.state==LEDGER_PENDING and row.receipt_id is None
 # 恢复重试：正常入箱一次
 process_pending(session,sub,sh(2026,11,14,8,31),arrival_rows,
  artifact_dir='/tmp/port-digest-test-artifacts')
 assert session.query(Receipt).filter_by(subscription_id=sub.id,report_day='2026-11-13').count()==1
 row=ledger_of(session,sub.id)[0]
 assert row.state==LEDGER_DELIVERED and row.receipt_id is not None

def test_orphan_receipt_reconciles_without_second_delivery(session):
 """模拟更极端情况：回执已落库而账本仍pending（如外部部分提交），重试只对账。"""
 add_arrival(session,'LG-1','北站',sh(2026,11,13,9,0),sh(2026,11,13,9,10))
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 materialize(session,sub,sh(2026,11,14,8,30))
 from src.services.digest import calculate
 payload=calculate(arrival_rows(session),'2026-11-13','北站')
 orphan=Receipt(subscription_id=sub.id,report_day='2026-11-13',recipient='北站值班室',
  created_at=iso(sh(2026,11,14,8,30)),state='delivered',payload=payload)
 session.add(orphan);session.commit()
 out=process_pending(session,sub,sh(2026,11,14,8,35),arrival_rows,
  artifact_dir='/tmp/port-digest-test-artifacts')
 assert out['reconciled']==[ledger_of(session,sub.id)[0].id]
 assert session.query(Receipt).filter_by(subscription_id=sub.id).count()==1
 assert ledger_of(session,sub.id)[0].state==LEDGER_DELIVERED

# ---------- 并发：计划修改与扫描的唯一归属 ----------
def test_ledger_unique_constraint_makes_ownership_unique(session):
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 materialize(session,sub,sh(2026,11,14,8,30))
 session.commit()
 snap=snapshot_at(session,sub.id,sh(2026,11,14,8,30))
 dup=DeliveryLedger(subscription_id=sub.id,snapshot_id=snap.id,due_at=iso(sh(2026,11,14,8,30)),
  report_day='2026-11-13',station='北站',recipient='北站值班室',state=LEDGER_PENDING)
 session.add(dup)
 from sqlalchemy.exc import IntegrityError
 with pytest.raises(IntegrityError):session.commit()
 session.rollback()
 assert len(ledger_of(session,sub.id))==1

def test_snapshot_at_is_unique_half_open_ownership(session):
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 change=sh(2026,11,15,10,0)
 open_snapshot(session,sub,change,'演练划界')
 session.commit()
 # 划界前一刻归旧快照，划界时点归新快照，半开区间无重叠无空档
 old=snapshot_at(session,sub.id,change-timedelta(seconds=1))
 new=snapshot_at(session,sub.id,change)
 assert old.id!=new.id and new.effective_from==utc_iso(change)
 assert old.effective_to==utc_iso(change)

def test_repeated_materialize_is_idempotent(session):
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 materialize(session,sub,sh(2026,11,20,9,0))
 first=len(ledger_of(session,sub.id))
 materialize(session,sub,sh(2026,11,20,9,0))
 run_due(session,sh(2026,11,20,9,0))
 receipts_after_run=session.query(Receipt).filter_by(subscription_id=sub.id).count()
 materialize(session,sub,sh(2026,11,20,9,0))
 # 再次扫描同日：只推进游标，不新增账本行也不新增回执
 run_due(session,sh(2026,11,20,9,1))
 assert len(ledger_of(session,sub.id))==first
 assert session.query(Receipt).filter_by(subscription_id=sub.id).count()==receipts_after_run

# ---------- 多次改计划叠加 ----------
def test_multiple_changes_during_outage_keep_each_eras_plan(session):
 add_arrival(session,'N1','北站',sh(2026,11,13,9,0),sh(2026,11,13,9,5))
 add_arrival(session,'S1','南站',sh(2026,11,14,9,0),sh(2026,11,14,9,5))
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 edit_sub(session,sub.id,name=sub.name,station='南站',first_due_at=iso(sh(2026,11,15,9,0)))
 edit_sub(session,sub.id,name=sub.name,station='东港',recipient='东港值班室',
  first_due_at=iso(sh(2026,11,16,9,0)))
 run_due(session,sh(2026,11,17,9,0))
 rows=ledger_of(session,sub.id)
 plan={r.report_day:(r.station,r.recipient) for r in rows}
 assert plan['2026-11-13']==('北站','北站值班室')
 assert plan['2026-11-14']==('北站','北站值班室')  # 应发15日08:30，首次改站09:00之前
 assert plan['2026-11-15']==('南站','北站值班室')  # 应发16日08:30，南站时段
 assert plan['2026-11-16']==('东港','东港值班室')  # 应发17日08:30，东港时段
 # 每个账本行都有人类可读的欠报依据
 assert all(r.basis and ('快照' in r.basis) for r in rows)

# ---------- 空日回执与复核约束 ----------
def test_empty_receipt_cannot_be_reviewed_but_can_be_exported(session):
 from fastapi import HTTPException
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 run_due(session,sh(2026,11,14,8,30))
 receipt=session.query(Receipt).filter_by(subscription_id=sub.id).one()
 assert receipt.state=='empty'
 with pytest.raises(HTTPException) as ei:
  r=session.get(Receipt,receipt.id)
  if r.state=='empty':raise HTTPException(409,'空日回执无晨报内容，不支持值班复核')
 assert ei.value.status_code==409

# ---------- 延误重算口径：追补与试算一致 ----------
def test_catchup_delay_math_matches_preview_method(session):
 for i,mins in enumerate((0,10,20,40)):
  add_arrival(session,f'LG-{i}','北站',sh(2026,11,13,8,0)+timedelta(hours=i),
   sh(2026,11,13,8,0)+timedelta(hours=i,minutes=mins))
 add_arrival(session,'LG-P','北站',sh(2026,11,13,20,0),None)
 sub=make_sub(session,first_due_at=iso(sh(2026,11,14,8,30)))
 run_due(session,sh(2026,11,14,8,30))
 p=session.query(Receipt).filter_by(subscription_id=sub.id,report_day='2026-11-13').one().payload
 assert p['scheduled']==5 and p['completed']==4 and p['pending']==1
 assert p['late']==3 and p['mean_delay']==17.5
 assert p['p50']==15.0  # 线性插值 (10,20) 中点
