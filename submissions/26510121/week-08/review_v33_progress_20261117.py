"""Frozen v33 readout; all market and journal access is read-only."""
from datetime import datetime, timezone
from pathlib import Path
import json
import sqlite3
import time

ROOT=Path(__file__).resolve().parent
BASE='logs/autonomous-deploy-before-20261117T000600153643Z.json'
SOURCE='logs/v28-progress-source-20261117T034009147309Z.json'
DIAG='logs/progress-diagnosis-20261117T034034355035Z.json'
EARLIER='logs/finalday-live-20261117T001049775007Z.json'


def read(path):
    return json.loads((ROOT/path).read_text(encoding='utf8'))


def main():
    baseline=read(BASE);source=read(SOURCE);diagnosis=read(DIAG);earlier=read(EARLIER)
    board=source['responses']['/api/board']['data']
    lo=baseline['at'];hi=source['responses']['/api/board']['finished_at']
    rank={}
    for role,metric,key in [('shops','all','name'),('buyers','all','name'),('players','total','player')]:
        current=next(r for r in board[role] if r[key]=='seochanit')
        old=next(r for r in baseline['board'][role] if r[key]=='seochanit')
        rank[role]={'before':1+sum(type(r.get(metric)) in (int,float) and r[metric]>old[metric] for r in baseline['board'][role]),
            'after':1+sum(type(r.get(metric)) in (int,float) and r[metric]>current[metric] for r in board[role]),
            'value_before':old[metric],'value_after':current[metric]}
    old_shops={r['name']:r for r in baseline['board']['shops']}
    now_shops={r['name']:r for r in board['shops']}
    top5=sorted((r for r in board['shops'] if type(r.get('all')) in (int,float)),key=lambda r:r['all'],reverse=True)[:5]
    peers=[]
    for name in ['seochanit']+[r['name'] for r in top5]:
        old,new=old_shops[name],now_shops[name]
        peers.append({'shop':name,'book_before':old['book'],'book_after':new['book'],
            'book_delta':new['book']-old['book'],'all':new['all']})
    events_sql='SELECT id,ts,data FROM events WHERE kind=? AND ts>=? AND ts<? ORDER BY ts,rowid'
    with sqlite3.connect((ROOT/'.market/r3.sqlite3').as_uri()+'?mode=ro',uri=True) as db:
        db.execute('BEGIN')
        def events(kind):
            return [dict(json.loads(raw),event_at=ts) for _,ts,raw in db.execute(events_sql,(kind,lo,hi))]
        def kv(key):
            r=db.execute('SELECT value FROM kv WHERE key=?',(key,)).fetchone()
            return json.loads(r[0]) if r else None
        kinds=('model_usage','cycle_supply_error','cycle_supervisor_error','analytical_role_error',
            'buyer_worker_error','market_control_error','shop_settled')
        counts={k:len(events(k)) for k in kinds}
        approval=kv('company-procurement:v30');control=kv('market_control')
        sales=events('shop_settled')
    inventory=read('.market/inventory.json')
    inventory_at=(ROOT/'.market/inventory.json').stat().st_mtime
    old_units=baseline['inventory']['units'];current_units={u['unit'] for u in inventory['units']}
    sold={s['unit'] for s in sales}
    expiry_candidates=[{k:u.get(k) for k in ('unit','sku','kind','cost','expires_at')} for u in old_units
        if u['unit'] not in current_units and u['unit'] not in sold
        and type(u.get('expires_at')) in (int,float) and u['expires_at']<=hi]
    after_last={}
    for role in ('shops','buyers'):
        current=next(r for r in board[role] if r['name']=='seochanit')
        old=earlier['own'][role]
        fields=('all','book','revenue','deals','stars_rank') if role=='shops' else ('all','saved','forum','deals','posts','surplus')
        after_last[role]={k:{'before':old[k],'after':current[k],'delta':current[k]-old[k]} for k in fields}
    out={'at':time.time(),'sources':[BASE,SOURCE,DIAG,EARLIER],'window':diagnosis['window'],
        'rank':rank,'own':diagnosis['own'],'versus_last_0910':after_last,'peer_book':peers,
        'revenue_overtake':{name:{'before':old_shops[name]['revenue'],'after':now_shops[name]['revenue']}
            for name in ('seochanit','buzllightyear')},
        'bridge':diagnosis['bridge'],'shop':diagnosis['shop'],'buyer':diagnosis['buyer'],
        'inventory':diagnosis['inventory'],'events_to_cutoff':counts,
        'later_observation':{'inventory_at':inventory_at,'procurement':approval,
            'control':{k:control.get(k) for k in ('at','status','pause_restock','cash_capacity')},
            'expiry_candidates':expiry_candidates,'expiry_candidate_cost':sum(u['cost'] for u in expiry_candidates),
            'limitation':'Missing from a later snapshot with elapsed expiry and no matched sale is an expiry signal, not confirmed disposal; old/unrecorded settlements may be missing.'},
        'queries':{'events':events_sql},
        'limitations':['09:06 paired board is the main comparable baseline; 09:10 own-account capture is reported separately.',
            'Local inventory is later than the public board cutoff; inventory changes are not identical to public stock rows.',
            'ALL ranks use published rounded values and include published house participants.',
            'Short post-release observations cannot isolate causal uplift or predict the closing rank.']}
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    path=ROOT/'logs'/('v33-progress-review-'+stamp+'.json')
    path.open('x',encoding='utf8').write(json.dumps(out,ensure_ascii=False,indent=2))
    receipt={'schemaVersion':1,'items':[{'id':'v33-progress','title':'Shop·Buyer 진행 경과',
        'queries':[{'id':'paired-board','source':{'label':'시장 공식 공개 점수표',
            'files':[BASE,SOURCE],
            'links':[{'label':'시장 공개 점수표','url':'https://market.ourolabs.ai/api/board'}],
            'metricDefinitions':[{'label':'Book','definition':'시장 공개 원장상의 현금·보유 재고 자산 지표.'},
                {'label':'ALL','definition':'시장 규칙의 구성 요소별 점수로 계산한 공개 종합 점수.'}],
            'filters':['같은 계정 seochanit 및 현재 Shop ALL 상위 5곳'],
            'caveats':['공개 점수는 반올림돼 있으며 상대적 순위에 따라 달라진다. 인과적 정책 효과 비교는 아니다.']},
            'reportingPeriod':'2026-11-17 09:06–12:40 KST',
            'columns':['shop','book_before','book_after','book_delta'],
            'rows':[{k:r[k] for k in ('shop','book_before','book_after','book_delta')} for r in peers],
            'preview':{'kind':'aggregate','note':'우리 계정과 현재 Shop ALL 상위 5곳의 같은 기간 Book 변화.'},
            'methods':[{'language':'calculation','code':'Book delta = published Book after - published Book before; rank = 1 + count(published ALL greater than own ALL).'}]},
            {'id':'execution','source':{'label':'로컬 거래·공급 실행 원장',
                'files':[DIAG],'tables':['events'],'sql':events_sql,
                'filters':['09:06 <= event timestamp < 12:40; own settled sales and supply execution status'],
                'caveats':['관측 매출총이익·환불과 공식 Book 차이의 잔차에는 보관·폐기·미관측 환불·집계 시점 차이가 포함될 수 있다.']},
                'reportingPeriod':'2026-11-17 09:06–12:40 KST'}]}]}
    receipt_path=ROOT/'logs'/('v33-progress-sources-'+stamp+'.json')
    receipt_path.open('x',encoding='utf8').write(json.dumps(receipt,ensure_ascii=False,indent=2))
    assert sum(r['gross_margin'] for r in out['shop']['mix'].values())==out['bridge']['recorded_gross_margin']
    assert out['bridge']['recorded_gross_margin']-out['bridge']['observed_refunds']-out['bridge']['unattributed_book_deductions']==out['bridge']['official_book_delta']
    assert sum(out['shop']['review_stars'].values())==out['shop']['review_count']
    assert rank['players']['value_after']==round(rank['shops']['value_after']+rank['buyers']['value_after'],3)
    print('SOURCE',path);print('RECEIPT',receipt_path)
    print('PASS: paired ranks, Book bridge, review totals and combined ALL reconcile.')


if __name__=='__main__':main()
