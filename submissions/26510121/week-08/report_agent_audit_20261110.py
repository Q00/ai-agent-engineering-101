"""Validate the readout and render input from frozen evidence; no live writes."""
from datetime import datetime,timezone,timedelta
from r3.state import ROOT,read_json,atomic_json
from audit_agent_system_20261110 import SOURCES
import json

AUDIT='logs/agent-system-audit-20261110T043032470360Z.json'
KST=timezone(timedelta(hours=9))

def main():
    audit=read_json(ROOT/AUDIT)
    assert sum(r['n'] for r in audit['decision_modes_24h'])==31274
    assert audit['model_usage']['last_24h']==0
    assert audit['buyer_funnel']['goods']['paid']==7 and audit['buyer_funnel']['goods']['closed']==185
    assert audit['memory_coverage']['public_settle']['nominal_window_count']==audit['memory_coverage']['public_settle']['selected_window_count']
    result={'source':AUDIT,'components':[],'guardrail':'Analysis only. No strategy, model, memory or market state changed.'}
    rows=[]
    for source in SOURCES:
        src=read_json(ROOT/source);board=src['responses']['/api/board']['data']
        point={'time':datetime.fromtimestamp(src['responses']['/api/board']['finished_at'],KST).isoformat()}
        ranks={}
        for role,metric_fields in [('shops',['book','revenue','stars_rank']),('buyers',['saved','deals','posts'])]:
            scored=[r for r in board[role] if type(r.get('all')) in (int,float)]
            own=next(r for r in scored if r['name']=='seochanit')
            point['Shop ALL' if role=='shops' else 'Buyer ALL']=own['all']
            ranks[role]={f:1+sum(r.get(f,float('-inf'))>own[f] for r in scored if type(r.get(f)) in (int,float)) for f in metric_fields}
        result['components'].append({'source':source,'at':point['time'],'ranks':ranks})
        rows.append(point)
    atomic_json(ROOT/'logs/agent-system-audit-readout-20261110.json',result)
    atomic_json(ROOT/'logs/agent-system-audit-chart-20261110.json',{
        'id':'agent-system-audit-all','title':'양쪽 자산·Saved 개선과 다른 ALL 흐름',
        'description':'2026-11-09 21:28→11-10 13:26 KST 공식 보드 관측. ALL 축은 0.75~0.87로 확대. 시점 간 연결은 관측 변화이며 인과 추정이 아닙니다.',
        'height':250,'spec':{'type':'line','x':'time','y':['Shop ALL','Buyer ALL'],'yLabel':'ALL 점수',
            'yDomain':[.75,.87],'startAtZero':False,'showValues':True,'showLegend':True,
            'colors':{'Shop ALL':'#167D9A','Buyer ALL':'#B55D22'},
            'valueFormat':{'style':'decimal','maximumFractionDigits':3}},'rows':rows})
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
