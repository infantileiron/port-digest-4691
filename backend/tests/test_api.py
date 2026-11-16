"""HTTP 层集成：鉴权、账本/回执接口、空日回执复核约束与完整追补链路。"""
from datetime import datetime,timedelta
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.config import database as dbmod
from src.models.entities import Base,User,Receipt
from src.utils.security import hash_password
from src.utils.time import SH,utc_iso

@pytest.fixture()
def client(tmp_path,monkeypatch):
 engine=create_engine('sqlite:///'+str(tmp_path/'api.db'))
 TestingSession=sessionmaker(bind=engine,expire_on_commit=False)
 Base.metadata.create_all(engine)
 monkeypatch.setattr(dbmod,'engine',engine)
 monkeypatch.setattr(dbmod,'Session',TestingSession)
 # 冻结墙钟在2026-11-14 08:00（上海），让接口内 now_utc() 与演练时钟一致
 import src.utils.time as timemod
 frozen=datetime(2026,11,14,8,0,tzinfo=SH)
 monkeypatch.setattr(timemod,'CLOCK',lambda:frozen)
 import src.services.scheduler as scheduler
 monkeypatch.setattr(scheduler,'artifact_dir',lambda:str(tmp_path/'artifacts'))
 s=TestingSession()
 for role in ('admin','analyst','viewer'):
  s.add(User(email=role+'@demo.local',role=role,password=hash_password('Demo123!')))
 s.commit();s.close()
 from src.main import app
 with TestClient(app) as c:yield c,TestingSession

def token(c,role):
 r=c.post('/auth/login',json={'email':role+'@demo.local','password':'Demo123!'});assert r.status_code==200
 return {'Authorization':'Bearer '+r.json()['token']}

def test_role_boundaries_and_ledger_endpoints(client):
 c,S=client
 admin=token(c,'admin');viewer=token(c,'viewer');analyst=token(c,'analyst')
 # viewer 不能改订阅
 r=c.post('/subscriptions',headers=viewer,json={'name':'北站晨报','station':'北站','daily_at':'08:30',
  'recipient':'北站值班室','enabled':True})
 assert r.status_code==403
 # admin 创建
 t0=datetime(2026,11,14,8,30,tzinfo=SH)
 r=c.post('/subscriptions',headers=admin,json={'name':'北站晨报','station':'北站','daily_at':'08:30',
  'recipient':'北站值班室','enabled':True,'first_due_at':utc_iso(t0)})
 assert r.status_code==200;sub_id=r.json()['id']
 # 账本立即可读；冻结时钟08:00早于08:30首次到点，故暂无已到应发时点
 r=c.get('/subscriptions/%d/ledger'%sub_id,headers=viewer);assert r.status_code==200
 assert r.json()==[]
 r=c.get('/ledger/summary',headers=viewer);assert r.status_code==200
 assert r.json()[0]['pending']==0
 # analyst 可读到发但不能配置订阅
 assert c.get('/arrivals',headers=analyst).status_code==200
 assert c.put('/subscriptions/%d'%sub_id,headers=analyst,json=
  {'name':'北站晨报','station':'北站','daily_at':'08:30','recipient':'北站值班室','enabled':False}).status_code==403

def test_full_catchup_via_api_with_empty_day_and_export(client):
 c,S=client
 admin=token(c,'admin')
 t0=datetime(2026,11,14,8,30,tzinfo=SH)
 sub=c.post('/subscriptions',headers=admin,json={'name':'南站晨报','station':'南站','daily_at':'08:30',
  'recipient':'南站值班室','enabled':True,'first_due_at':utc_iso(t0)}).json()
 # 录入一条13日南站班次
 r=c.post('/arrivals',headers=token(c,'analyst'),json={'code':'S-1','station':'南站',
  'planned_at':utc_iso(datetime(2026,11,13,9,0,tzinfo=SH)),
  'actual_at':utc_iso(datetime(2026,11,13,9,12,tzinfo=SH)),'note':'演练'})
 assert r.status_code==200
 # 模拟停机到16日恢复：直接驱动调度（与worker同一入口）
 from src.services.scheduler import run_due
 with S() as s:run_due(s,datetime(2026,11,16,9,0,tzinfo=SH))
 receipts=c.get('/receipts',headers=admin).json()
 days={r['report_day']:r for r in receipts}
 assert set(days)=={'2026-11-13','2026-11-14','2026-11-15'}
 assert days['2026-11-13']['state']=='delivered' and days['2026-11-14']['state']=='empty'
 # 空日回执不可复核，正常回执可复核
 empty_id=days['2026-11-14']['id']
 assert c.post('/receipts/%d/review'%empty_id,headers=admin,json={'note':'这条应该被拒绝'}).status_code==409
 ok_id=days['2026-11-13']['id']
 assert c.post('/receipts/%d/review'%ok_id,headers=admin,json={'note':'值班核对无误，延误口径一致'}).status_code==200
 assert c.post('/receipts/%d/review'%ok_id,headers=admin,json={'note':'复核不可覆盖'}).status_code==409
 # 导出冻结 JSON，含来源与账本溯源
 export_r=c.get('/receipts/%d/export'%ok_id,headers=token(c,'viewer'))
 assert export_r.status_code==200
 body=export_r.json()
 assert body['payload']['provenance']['ledger_id']>0
 assert body['payload']['source_codes']==['S-1']
 # 账本汇总：无欠报
 summ=c.get('/ledger/summary',headers=admin).json()
 mine=[x for x in summ if x['subscription_id']==sub['id']][0]
 assert mine['pending']==0 and mine['delivered']==1 and mine['empty']==2

def test_legacy_receipt_remains_readable_through_api(client):
 c,S=client
 admin=token(c,'admin')
 with S() as s:
  s.add(Receipt(subscription_id=None,report_day='2026-11-10',recipient='旧箱',
   created_at=utc_iso(datetime(2026,11,11,8,30,tzinfo=SH)),state='delivered',
   payload={'report_day':'2026-11-10','scheduled':1,'completed':1,'pending':0,'p50':0.0,
    'p95':0.0,'late_rate':0.0,'checksum':'legacy','rows':[],'delays':[]}))
  s.commit()
 r=c.get('/receipts',headers=admin);assert r.status_code==200
 got=r.json()[0]
 assert got['payload']['checksum']=='legacy' and 'provenance' not in got['payload']
