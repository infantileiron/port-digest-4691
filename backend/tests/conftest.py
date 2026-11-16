import os
os.environ.setdefault('DATABASE_URL','sqlite://')
os.environ.setdefault('JWT_SECRET','test-secret-3113')
os.environ.setdefault('REDIS_URL','redis://localhost:6379/0')
os.environ.setdefault('ARTIFACT_DIR','/tmp/port-digest-test-artifacts')
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.config import database as dbmod
from src.models.entities import Base,User
from src.utils.security import hash_password

@pytest.fixture()
def session(tmp_path,monkeypatch):
 engine=create_engine('sqlite:///'+str(tmp_path/'test.db'))
 TestingSession=sessionmaker(bind=engine,expire_on_commit=False)
 Base.metadata.create_all(engine)
 monkeypatch.setattr(dbmod,'engine',engine)
 monkeypatch.setattr(dbmod,'Session',TestingSession)
 import src.services.ledger as ledger
 import src.services.scheduler as scheduler
 monkeypatch.setattr(scheduler,'artifact_dir',lambda:str(tmp_path/'artifacts'))
 s=TestingSession()
 for role in ('admin','analyst','viewer'):
  s.add(User(email=role+'@demo.local',role=role,password=hash_password('Demo123!')))
 s.commit()
 yield s
 s.close()
