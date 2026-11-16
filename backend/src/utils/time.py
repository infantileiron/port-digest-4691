from datetime import datetime,timedelta,timezone,time
from zoneinfo import ZoneInfo
SH=ZoneInfo('Asia/Shanghai')
def aware(value):
 d=datetime.fromisoformat(value) if isinstance(value,str) else value
 if d.tzinfo is None:raise ValueError('时间必须包含时区偏移，例如+08:00')
 return d.astimezone(timezone.utc)
def now_utc():
 """当前UTC；测试可 monkeypatch src.utils.time.CLOCK 注入固定时钟。"""
 return CLOCK().astimezone(timezone.utc)
CLOCK=lambda:datetime.now(timezone.utc)
def utc_iso(value):
 """统一UTC序列化：始终以+00:00结尾，保证SQLite/PostgreSQL下字符串字典序即时间序。"""
 return aware(value).isoformat()
def next_due(daily_at,now):
 h,m=map(int,daily_at.split(':'));local=now.astimezone(SH);candidate=local.replace(hour=h,minute=m,second=0,microsecond=0)
 if candidate<=local:candidate+=timedelta(days=1)
 return candidate.astimezone(timezone.utc)
def yesterday(now):return (now.astimezone(SH).date()-timedelta(days=1)).isoformat()
def sh_date(value):
 """上海自然日；入参为带偏移时间（字符串或datetime）。"""
 return (aware(value) if isinstance(value,str) else value).astimezone(SH).date()
def occurrence_utc(daily_at,day):
 """上海时区某天的HH:MM应发时刻（UTC）。"""
 h,m=map(int,daily_at.split(':'))
 return datetime.combine(day,time(hour=h,minute=m),tzinfo=SH).astimezone(timezone.utc)
def report_day_for_due(due_at):
 """应发时点对应的“前一日”报告日：以上海日历，取应发时刻所在自然日的前一天。"""
 return (due_at.astimezone(SH).date()-timedelta(days=1)).isoformat()
