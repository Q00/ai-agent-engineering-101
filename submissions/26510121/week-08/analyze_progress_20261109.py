"""Frozen board comparison and order-cohort diagnostics; no trading or policy edits."""
from collections import Counter
from datetime import datetime,timezone,timedelta
import json
import math
import sqlite3
from r3.state import ROOT,read_json,atomic_json

OLD='logs/peer-market-source-20261109T052720647141Z.json'
NEW='logs/peer-market-source-20261109T074127697069Z.json'
LOCAL='logs/championship-live-evidence-20261109T074049670738Z.json'
KST=timezone(timedelta(hours=9))


def own(board,table):return next(r for r in board[table] if r.get('name',r.get('player'))=='seochanit')
def main():
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    old,new=[read_json(ROOT/p) for p in [OLD,NEW]]
    a,b=[s['responses']['/api/board']['data'] for s in [old,new]]
    t0,t1=[s['responses']['/api/board']['finished_at'] for s in [old,new]]
    result={'sources':[OLD,NEW,LOCAL],'start':t0,'end':t1,'hours':(t1-t0)/3600,
        'rules_unchanged':old['responses']['/agent.md']['data']==new['responses']['/agent.md']['data'],
        'official':{},'peer_changes':{},'raw_components':{}}
    for table,metric,fields in [('shops','all',['book','revenue','deals','stock_units','stock_cost','stars_rank']),
                                ('buyers','all',['saved','deals','forum','posts']),('players','total',['total'])]:
        prior,current=own(a,table),own(b,table)
        def rank(board,row,field):
            rows=[r for r in board[table] if type(r.get(field)) in (int,float) and type(r.get(metric)) in (int,float)]
            return {'rank':1+sum(r[field]>row[field] for r in rows),'population':len(rows)}
        result['official'][table]={'before':{k:prior.get(k) for k in [*fields,metric]},
            'after':{k:current.get(k) for k in [*fields,metric]},'before_rank':rank(a,prior,metric),
            'after_rank':rank(b,current,metric)}
        result['raw_components'][table]={f:{'before':rank(a,prior,f),'after':rank(b,current,f)} for f in fields}
        oldrows={r.get('name',r.get('player')):r for r in a[table]}
        peers=[]
        for r in b[table]:
            name=r.get('name',r.get('player'));p=oldrows.get(name,{})
            changes={f:r[f]-p[f] for f in [*fields,metric] if type(r.get(f)) in (int,float) and type(p.get(f)) in (int,float)}
            peers.append({'name':name,'all':r.get(metric),'delta':changes,
                          'book_per_hour':changes.get('book',0)/result['hours'],
                          'deals_per_hour':changes.get('deals',0)/result['hours']})
        result['peer_changes'][table]=sorted(peers,key=lambda r:r.get('all') or -1,reverse=True)
    local=read_json(ROOT/LOCAL);supplies=local['successful_supplies']
    supply=[s for s in supplies if t0<datetime.fromisoformat('2026-11-09T'+s['time_kst']).replace(tzinfo=KST).timestamp()<=t1]
    result['supply_window']={'units':sum(s['qty'] for s in supply),'cost':sum(s['cost'] for s in supply)}
    stock=own(b,'shops');result['cash_from_official_book']=stock['book']+5000000-stock['stock_cost']
    result['cash_capacity']={k:v for k,v in local['control']['cash_capacity'].items() if k!='reserve_release_schedule'}
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as c:
        c.execute('BEGIN')
        def kv(k):return json.loads(c.execute('SELECT value FROM kv WHERE key=?',(k,)).fetchone()[0])
        epoch=kv('buyer_revision_epoch');span=t1-epoch
        query='SELECT ts,data FROM events WHERE kind=? AND ts>? AND ts<=? ORDER BY ts'
        def events(k,start,end):return [dict(json.loads(d),_ts=ts) for ts,d in c.execute(query,(k,start,end))]
        def cohort(start,end):
            orders={r['order']:r for r in events('buyer_order',start,end)}
            results={r['order']:r for r in events('market_result',start,end) if r['order'] in orders}
            purchases={r['order']:r for r in events('purchase',start,end) if r['order'] in orders}
            outcomes={r['order']:r for r in events('outcome',start,end) if r['order'] in orders}
            quotes=[r for r in events('quote_comparison',start,end) if r['order'] in orders]
            attempts=[r for r in events('attempt',start,end) if r['order'] in orders]
            summary={}
            for kind in ['food','goods']:
                ids={oid for oid,r in orders.items() if r.get('kind')==kind}
                finished=ids & results.keys();paid=ids & purchases.keys()
                paid_results={oid for oid in finished if results[oid].get('verified')}
                ps=[purchases[oid] for oid in paid]
                qs=[r for r in quotes if r['order'] in ids]
                technical={r['order'] for r in attempts if r['order'] in ids and
                           (r.get('error_type') in ['ReadTimeout','ConnectError','HTTPStatusError'] or
                            r.get('kind') in ['ReadTimeout','ConnectError','HTTPStatusError'] or
                            r.get('reason')=='ambiguous_ask')}
                summary[kind]={'orders':len(ids),'completed_results':len(finished),'verified':len(paid_results),
                    'purchases':len(paid),'pending':len(ids-finished),'closed_conversion':len(paid_results)/len(finished) if finished else None,
                    'initial_saved_mean':sum(p['initial_saved_contribution'] for p in ps)/len(ps) if ps else None,
                    'mature_paid':sum(bool(outcomes.get(oid,{}).get('matured')) for oid in paid),
                    'broken_paid':sum(bool(outcomes.get(oid,{}).get('broken')) for oid in paid),
                    'technical_affected_orders':len(technical),'quotes':len(qs),
                    'feasible_at_ask':sum(bool(q.get('feasible_at_ask')) for q in qs),
                    'priced_above_cap':sum(q['ask']>q['assessment']['cap'] for q in qs),
                    'grade_declared':sum((q['assessment'].get('required_grade') or q['assessment']['risk_evidence'].get('grade'))
                                         in ['A','B','C'] and 'grade' not in q['assessment'].get('conditional_terms',[]) for q in qs),
                    'grade_conditional':sum('grade' in q['assessment'].get('conditional_terms',[]) for q in qs),
                    'public_quality_quotes':sum('cohort' in q['assessment']['risk_evidence'] for q in qs),
                    'paid_details':[{k:p.get(k) for k in ['order','sku','grade','warranty_hours','price','value','initial_saved_contribution']} for p in ps] if kind=='goods' else []}
            paid=list(purchases.values())
            return {'start':start,'end':end,'hours':(end-start)/3600,'by_kind':summary,
                    'total_paid':len(paid),'initial_saved_mean':sum(p['initial_saved_contribution'] for p in paid)/len(paid) if paid else None,
                    'attempt_types':dict(Counter(r.get('reason') or r.get('error_type') or r.get('kind') for r in attempts)),
                    'clarifications':len(events('buyer_terms_clarified',start,end))}
        result['buyer_equal_windows']={'before':cohort(epoch-span,epoch),'v11':cohort(epoch,t1)}
        result['sql']={'events':query,'parameters':'kind, exclusive cohort start, inclusive frozen board end; matched by order ID'}
    output=ROOT/'logs'/f'progress-cohort-analysis-{stamp}.json';atomic_json(output,result)
    print('SOURCE',output)
    print(json.dumps({k:v for k,v in result.items() if k not in ['peer_changes','sql']},ensure_ascii=True))
    rows=[]
    for source in [read_json(ROOT/'logs/autonomous-deploy-before-20261109T030859008302Z.json'),old,new]:
        board=source.get('board') or source['responses']['/api/board']['data']
        ts=source.get('at') or source['responses']['/api/board']['finished_at']
        rows.append({'time':datetime.fromtimestamp(ts,KST).isoformat(),'Shop ALL':own(board,'shops')['all'],
                     'Buyer ALL':own(board,'buyers')['all']})
    atomic_json(ROOT/'logs'/f'progress-all-chart-{stamp}.json',{
        'id':'shop-buyer-progress-20261109','title':'Shop 개선과 Buyer 상대 점수 하락',
        'description':'2026-11-09 KST 공식 보드 관측. ALL 점수 축은 0.75–0.85로 확대했다. 시작 저장본은 캐시된 보드다.',
        'height':280,'spec':{'type':'line','x':'time','y':['Shop ALL','Buyer ALL'],'yLabel':'ALL 점수',
            'yDomain':[.75,.85],'startAtZero':False,'showValues':True,'showLegend':True,
            'colors':{'Shop ALL':'#167D9A','Buyer ALL':'#B55D22'},'valueFormat':{'style':'decimal','maximumFractionDigits':3}},'rows':rows})
    atomic_json(ROOT/'logs'/f'progress-sources-{stamp}.json',{'schemaVersion':1,'items':[
        {'id':'official-progress','title':'공식 순위와 점수 변화','queries':[{'id':'board-comparison',
          'source':{'label':'시장 공식 board 저장본','files':[OLD,NEW],
            'links':[{'label':'시장 공식 보드','url':'https://market.ourolabs.ai/api/board'}],
            'filters':['계정: seochanit','순위: ALL/total 숫자가 공개된 전체 참가자'],
            'metricDefinitions':[{'label':'ALL','definition':'Shop은 Book 50%, 매출 30%, 계정별 평균 별점 20%. Buyer는 Saved 백분위 50%, forum 원점수 30%, 거래 10%, 게시 10%.'}],
            'caveats':['공개 점수의 동점은 공동 순위로 계산했다. 보드 캐시와 로컬 이벤트의 갱신 시점은 다를 수 있다.']},
          'reportingPeriod':'2026-11-09 14:27:21–16:41:29 KST',
          'columns':['role','before','after'], 'rows':[{'role':k,'before':v['before_rank']['rank'],'after':v['after_rank']['rank']} for k,v in result['official'].items()]}]},
        {'id':'buyer-cohort','title':'Buyer v11 주문과 실제 결제','queries':[{'id':'matched-orders',
          'source':{'label':'로컬 거래 원장과 공식 정산 결과','files':['analyze_progress_20261109.py',str(output.relative_to(ROOT)).replace('\\','/')],
            'sql':query,'filters':['주문 발생 시각 기준으로 동일 길이의 이전/새 버전 구간 비교','결과·구매·견적은 같은 order ID로 연결','구간 종료 후 결과 제외'],
            'caveats':['인접 구간의 단순 관측 비교로 전략 변경의 인과 효과를 확정하지 않는다. 새 공산품 구매는 6시간 품질 성숙 전이다. 견적과 실패 시도는 한 주문에서 반복될 수 있다.']},
          'reportingPeriod':datetime.fromtimestamp(epoch,KST).isoformat()+'–'+datetime.fromtimestamp(t1,KST).isoformat(),
          'columns':['kind','orders','completed','paid'],
          'rows':[{'kind':k,'orders':v['orders'],'completed':v['completed_results'],'paid':v['verified']} for k,v in result['buyer_equal_windows']['v11']['by_kind'].items()]}]}]})


if __name__=='__main__':main()
