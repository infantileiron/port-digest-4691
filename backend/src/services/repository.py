from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models.entities import Arrival,Subscription,Receipt
from src.services.business import dump
from src.services.ledger import open_snapshot,materialize,DEMARCATING_FIELDS
from src.utils.time import now_utc,utc_iso,next_due

def arrival_rows(s):return [dump(r) for r in s.query(Arrival).order_by(Arrival.planned_at,Arrival.id).all()]
def save_arrival(s,data,i=None):
 r=s.get(Arrival,i) if i else Arrival()
 if not r:raise HTTPException(404,'到发记录不存在')
 for k,v in data.model_dump().items():setattr(r,k,v)
 s.add(r)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'班次编号重复')
 return r

def save_subscription(s,data,i=None,now=None):
 r=s.get(Subscription,i) if i else Subscription();new=i is None
 if not r:raise HTTPException(404,'订阅不存在')
 now=now or now_utc()
 values=data.model_dump();first=values.pop('first_due_at')
 changes={k for k in DEMARCATING_FIELDS if not new and getattr(r,k)!=values[k]}
 for k,v in values.items():setattr(r,k,v)
 if new:
  # 首次到点沿用原规则：显式时间优先，否则安排下一个每日时刻。
  r.next_run_at=utc_iso(first) if first else utc_iso(next_due(r.daily_at,now))
  r.last_run_at=None
  s.add(r);s.flush()
  open_snapshot(s,r,now,'创建订阅，计划自此时点生效')
 else:
  if changes:open_snapshot(s,r,now,'订阅改计划：'+'、'.join(sorted(changes)))
  if first:
   r.next_run_at=utc_iso(first)
  elif changes:
   # 改时刻/暂停恢复从生效时点划界：重排下一个到点；历史欠报由账本按旧计划追补。
   r.next_run_at=utc_iso(next_due(r.daily_at,now))
  # 未填首次时间且无划界字段变更：保留原游标，避免抹掉待追补的欠报。
 s.add(r);s.flush()
 # 立刻把截至当前的应发时点落入账本，暂停区间不生成欠报。
 materialize(s,r,now)
 try:s.commit()
 except IntegrityError:s.rollback();raise HTTPException(409,'订阅名称重复或账本冲突，请重试')
 return r

def get_receipt(s,i):
 r=s.get(Receipt,i)
 if not r:raise HTTPException(404,'回执不存在')
 return r
