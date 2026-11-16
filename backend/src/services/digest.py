import json,hashlib
from datetime import date
from src.utils.time import aware,SH
VERSION='planned-day-linear-quantile-v1'
def quantile(values,p):
 if not values:return None
 values=sorted(values);position=(len(values)-1)*p;lower=int(position);upper=min(lower+1,len(values)-1)
 return values[lower]+(values[upper]-values[lower])*(position-lower)
def calculate(rows,report_day,station='全部',provenance=None):
 day=date.fromisoformat(report_day);selected=[r for r in rows if aware(r['planned_at']).astimezone(SH).date()==day and (station=='全部' or r['station']==station)]
 completed=[r for r in selected if r['actual_at'] is not None]
 delays=[max(0,(aware(r['actual_at'])-aware(r['planned_at'])).total_seconds()/60) for r in completed]
 late=sum(d>0 for d in delays)
 result=dict(report_day=report_day,timezone='Asia/Shanghai',station=station,scheduled=len(selected),completed=len(completed),pending=len(selected)-len(completed),late=late,late_rate=late/len(completed) if completed else None,p50=quantile(delays,.5),p95=quantile(delays,.95),mean_delay=sum(delays)/len(delays) if delays else None,max_delay=max(delays) if delays else None,rows=selected,delays=delays,checksum=hashlib.sha256(json.dumps(selected,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),version=VERSION,method='按计划到发时间的上海自然日归档；延误=max(0,实际−计划)分钟；未到发不进入分位数/延误率分母。P50/P95为(n−1)p线性插值；无已到发样本为null，不伪造0。')
 result['empty_day']=(len(selected)==0)
 result['source_codes']=[r['code'] for r in selected]
 result['source_count']=len(selected)
 if provenance:
  result['provenance']=provenance
  result['method']=result['method']+' 追补口径：按应发时点当时生效的计划快照筛站，来源与checksum随回执冻结，空日也留回执。'
 return result
