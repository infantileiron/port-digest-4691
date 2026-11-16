from sqlalchemy import Column,Integer,String,Float,JSON,Text,Boolean,ForeignKey,UniqueConstraint,Index
from src.config.database import Base
class User(Base):
 __tablename__='users'
 id=Column(Integer,primary_key=True);email=Column(String(100),unique=True,nullable=False);password=Column(String(200));role=Column(String(20))
class Arrival(Base):
 __tablename__='arrivals'
 id=Column(Integer,primary_key=True);code=Column(String(60),unique=True,nullable=False);station=Column(String(30));planned_at=Column(String(40));actual_at=Column(String(40),nullable=True);note=Column(Text,default='')
class Subscription(Base):
 __tablename__='subscriptions'
 id=Column(Integer,primary_key=True);name=Column(String(100),unique=True,nullable=False);station=Column(String(30));daily_at=Column(String(5));recipient=Column(String(80));enabled=Column(Boolean,default=False);next_run_at=Column(String(40));last_run_at=Column(String(40),nullable=True)
class SubscriptionSnapshot(Base):
 """订阅计划的生效划界版本：改站/改收件箱/改时刻/暂停恢复都新开一段，旧段闭合。"""
 __tablename__='subscription_snapshots'
 __table_args__=(UniqueConstraint('subscription_id','effective_from',name='uq_snapshot_sub_effective'),)
 id=Column(Integer,primary_key=True)
 subscription_id=Column(Integer,ForeignKey('subscriptions.id'),nullable=False)
 effective_from=Column(String(40),nullable=False)
 effective_to=Column(String(40),nullable=True)
 station=Column(String(30),nullable=False)
 daily_at=Column(String(5),nullable=False)
 recipient=Column(String(80),nullable=False)
 enabled=Column(Boolean,nullable=False)
 change_reason=Column(String(200),default='')
class DeliveryLedger(Base):
 """上海日历追补计划账本：每个应发时点一行，归属唯一快照，状态机串行推进。"""
 __tablename__='delivery_ledger'
 __table_args__=(UniqueConstraint('subscription_id','due_at',name='uq_ledger_sub_due'),
  Index('ix_ledger_sub_state','subscription_id','state'),
  Index('ix_ledger_due','due_at'))
 id=Column(Integer,primary_key=True)
 subscription_id=Column(Integer,ForeignKey('subscriptions.id'),nullable=False)
 snapshot_id=Column(Integer,ForeignKey('subscription_snapshots.id'),nullable=False)
 due_at=Column(String(40),nullable=False)
 report_day=Column(String(10),nullable=False)
 station=Column(String(30),nullable=False)
 recipient=Column(String(80),nullable=False)
 state=Column(String(20),nullable=False,default='pending')
 receipt_id=Column(Integer,ForeignKey('receipts.id'),nullable=True)
 attempts=Column(Integer,nullable=False,default=0)
 last_attempt_at=Column(String(40),nullable=True)
 basis=Column(Text,default='')
class Receipt(Base):
 __tablename__='receipts'
 __table_args__=(UniqueConstraint('subscription_id','report_day',name='uq_receipt_subscription_day'),)
 id=Column(Integer,primary_key=True);subscription_id=Column(Integer,ForeignKey('subscriptions.id'));report_day=Column(String(10));recipient=Column(String(80));created_at=Column(String(40));state=Column(String(20),default='delivered');payload=Column(JSON);review_note=Column(Text,default='')
class Audit(Base):
 __tablename__='audits'
 id=Column(Integer,primary_key=True);actor=Column(String(100));action=Column(String(200));created=Column(String(40))
