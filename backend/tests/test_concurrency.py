"""并发演练：多会话同时扫描与计划修改，确认归属唯一、不重复入箱。

SQLite 用文件库 + 线程串行化写，PostgreSQL 生产环境另有 SELECT ... FOR UPDATE SKIP LOCKED
行锁；这里验证应用层唯一约束与幂等对账在交叉提交下仍然成立。
"""
import threading
from datetime import datetime
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.models.entities import Base,User,DeliveryLedger,Receipt,Subscription
from src.utils.security import hash_password
from src.schemas.business import SubscriptionInput,ArrivalInput
from src.services.repository import save_subscription,save_arrival
from src.services.scheduler import run_due
from src.utils.time import SH,utc_iso

@pytest.fixture()
def shared(tmp_path):
 engine=create_engine('sqlite:///'+str(tmp_path/'conc.db'),
  connect_args={'timeout':30,'check_same_thread':False})
 S=sessionmaker(bind=engine,expire_on_commit=False)
 Base.metadata.create_all(engine)
 s=S();s.add(User(email='admin@demo.local',role='admin',password=hash_password('Demo123!')));s.commit();s.close()
 yield S
 engine.dispose()

def test_concurrent_scans_deliver_each_due_point_once(shared):
 S=shared
 t0=datetime(2026,11,14,8,30,tzinfo=SH)
 with S() as s:
  sub=save_subscription(s,SubscriptionInput(name='并发晨报',station='北站',daily_at='08:30',
   recipient='北站值班室',enabled=True,first_due_at=utc_iso(t0)),now=datetime(2026,11,14,8,0,tzinfo=SH))
  sub_id=sub.id
  for i in range(6):
   save_arrival(s,ArrivalInput(code='C-%d'%i,station='北站',
    planned_at=utc_iso(datetime(2026,11,13,8+i,0,tzinfo=SH)),
    actual_at=utc_iso(datetime(2026,11,13,8+i,7,tzinfo=SH)),note='并发演练'))
 recover=datetime(2026,11,18,9,0,tzinfo=SH)
 errors=[]
 def worker():
  try:
   with S() as s:run_due(s,recover)
  except Exception as e:errors.append(repr(e))
 threads=[threading.Thread(target=worker) for _ in range(6)]
 for t in threads:t.start()
 for t in threads:t.join()
 assert not errors,errors
 with S() as s:
  ledger=s.query(DeliveryLedger).filter_by(subscription_id=sub_id).all()
  dues=[r.due_at for r in ledger]
  # 14至18日每天08:30共5个应发时点
  assert len(dues)==len(set(dues))==5
  receipts=s.query(Receipt).filter_by(subscription_id=sub_id).all()
  days=[r.report_day for r in receipts]
  assert len(days)==len(set(days))==5
  assert all(r.state in ('delivered','empty') for r in ledger)

def test_concurrent_plan_edit_and_scan_keep_ownership_consistent(shared):
 S=shared
 t0=datetime(2026,11,14,8,30,tzinfo=SH)
 with S() as s:
  sub=save_subscription(s,SubscriptionInput(name='划界晨报',station='北站',daily_at='08:30',
   recipient='北站值班室',enabled=True,first_due_at=utc_iso(t0)),now=datetime(2026,11,14,8,0,tzinfo=SH))
  sub_id=sub.id
 barrier=threading.Barrier(2)
 errors=[]
 def scan():
  try:
   barrier.wait()
   with S() as s:run_due(s,datetime(2026,11,16,9,0,tzinfo=SH))
  except Exception as e:errors.append(repr(e))
 def edit():
  try:
   barrier.wait()
   with S() as s:
    save_subscription(s,SubscriptionInput(name='划界晨报',station='南站',daily_at='08:30',
     recipient='南站值班室',enabled=True,first_due_at=utc_iso(datetime(2026,11,15,12,0,tzinfo=SH))),
     sub_id,now=datetime(2026,11,15,12,0,tzinfo=SH))
  except Exception as e:errors.append(repr(e))
 ts=[threading.Thread(target=scan),threading.Thread(target=edit)]
 for t in ts:t.start()
 for t in ts:t.join()
 assert not errors,errors
 with S() as s:
  # 再跑一次扫描收敛：无论交叠顺序如何，最终每个应发时点恰好一行、回执不重复
  run_due(s,datetime(2026,11,17,9,0,tzinfo=SH))
  ledger=s.query(DeliveryLedger).filter_by(subscription_id=sub_id).all()
  assert len(ledger)==len({r.due_at for r in ledger})
  receipts=s.query(Receipt).filter_by(subscription_id=sub_id).all()
  assert len(receipts)==len({r.report_day for r in receipts})
  assert s.get(Subscription,sub_id).station=='南站'
