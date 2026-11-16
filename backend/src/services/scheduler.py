"""到点调度：每秒扫描，先按上海日历补齐账本，再逐个追补欠报。

首次到点沿用原规则（save_subscription 写入的 next_run_at 不变）；之后由账本接管，
服务中断恢复时把停机期间所有欠报按当时计划逐时点补入收件箱，空日也留回执。
"""
import os
from sqlalchemy import select
from src.models.entities import Subscription
from src.services.repository import arrival_rows
from src.services.ledger import lock_subscription,materialize,process_pending
from src.utils.time import aware,utc_iso

def artifact_dir():
 return os.environ.get('ARTIFACT_DIR','/artifacts')

def run_due(s,now,crash_hook=None):
 result=dict(scanned=0,delivered=[],empty=[],reconciled=[],pending=0,skipped=0)
 now_iso=utc_iso(now)
 due_subs=s.scalars(select(Subscription).where(
  Subscription.next_run_at<=now_iso).order_by(Subscription.id.asc())).all()
 for sub0 in due_subs:
  sub=lock_subscription(s,sub0.id)
  if not sub or aware(sub.next_run_at)>now:continue
  result['scanned']+=1
  # 无论启用/暂停都要先补账本：暂停区间标 skipped_pause，启用区间欠报为 pending。
  materialize(s,sub,now)
  if not sub.enabled:
   # 暂停：不追补、不推进游标；恢复时由修订接口重排下一个到点。
   s.commit();continue
  summary=process_pending(s,sub,now,arrival_rows,artifact_dir=artifact_dir(),crash_hook=crash_hook)
  result['delivered']+=summary['delivered'];result['empty']+=summary['empty']
  result['reconciled']+=summary['reconciled'];result['skipped']+=summary['skipped']
 s.commit()
 return result
