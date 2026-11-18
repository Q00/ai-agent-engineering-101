"""One frozen six-hour cohort, six-hour maturation, one model-free batch review."""
import hashlib
import json
import math
import os
import sqlite3
import statistics
import time
from collections import defaultdict,Counter
from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
from urllib.request import urlopen
from optimizer import atomic,read
from shop_profit import ownership_tags

ROOT=Path(__file__).resolve().parent
MD=ROOT/'.market'
STATE=MD/'strategy-batch-review.json'
KST=timezone(timedelta(hours=9))

STRATEGIES=[
 ('capital','전액 투자 가능·강제 소진 없음','현금 사용 자체가 수익은 아니다. 공급 예약만 중복 차감한다.','Book·실현 이익·미판매 재고'),
 ('demand','실제 판매·유효 문의로 수요 계산','고정 50% 점유 가정이 과잉 매입을 유발했다.','새 매입의 판매율·보유시간'),
 ('grades','실제 도착 등급으로 기대 판매가 계산','판매된 A등급 구성을 무작위 입고 물량에 적용하면 이익이 과대 추정된다.','등급별 원가·성숙 순이익'),
 ('allocation','판매 가능한 순이익/시간 우선','큰 매출보다 회수 가능한 이익과 자본 회전이 우선이다.','신규 매입 코호트의 원가 확인 이익'),
 ('cover','다음 실제 공급 기회까지의 재고·관측 품절 버퍼','기존 등급별 잔여 재고를 차감하고 과잉 재고를 먼저 회전한다.','품절·판매율·미판매 비중'),
 ('pricing','SKU→품목군·등급→전체 등급 계층 학습','희귀 상품은 검증된 유사 자료를 사용하고 오래된 champion을 재검증한다.','동일 가격 에피소드의 문의·정산·성숙 순기여'),
 ('concession','재고 압력·Book·Revenue·계정별 Stars 동적 양보','추가 양보의 성립 가치와 기다릴 기회비용을 비교한다.','성립률·성숙 순기여; 독립 효과는 분리 불가'),
 ('floor','원가·보관료·대기 가치 하한','일률적인 8% 기준 대신 실제 수요의 기다릴 가치를 반영한다.','손실 거래·마진·판매 지연'),
 ('warranty','6시간 고장 위험·최대 72시간 계약','6시간 이후의 위험을 반복 확대하지 않는다.','관찰 코호트의 성숙 환불'),
 ('buyer','마진 점유율·등급·보증 평가','Patch 3.6과 실제 고장 관측에 맞춰 C등급 우대를 제거했다.','Saved·구매 등급·고장·환불'),
 ('search','실제 견적·다음 주문 기회비용·단일 결제','고정 Saved 조기 종료와 의무 반복 입찰 대신 시간당 기대 ALL을 비교한다.','공개 주문→결과 시간·정산·다음 주문 대기'),
 ('forum','양쪽 역할의 원장·실제 Forum 평점·Class Posts','좋아요 요청 없이 실제 평점과 게시 점수의 변화를 비교한다.','실제 Forum 평점·Class Posts·노출된 경우만 고유 투표자'),
 ('operations','기존 작업자·캐시·WAL·모델 호출 없음','중복 작업과 잠금·해석 비용을 줄인다.','수집 상태·가격 기회·설정 변경 여부'),
 ('retired','강제 소진·65% 양보·8% 고정 하한·C 우대 폐기','근거 부족 또는 목표와 충돌해 폐기한 제안이다.','재활성화하지 않고 이력을 보존'),
]


def stamp(ts):return datetime.fromtimestamp(ts,KST).isoformat(timespec='seconds')


def hashes():
    names=[p for p in ROOT.glob('*.py') if p.name not in ('batch_strategy_review.py','strategy_watch.py')]
    names += [ROOT/n for n in ['strategy-policy.json','profit-policy.json','adaptive-policy.json','forum-policy.json','agent.json','seller-access.json']]
    return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in names if p.exists()}


def board_snapshot():
    with urlopen('https://market.ourolabs.ai/api/board?round=3',timeout=15) as r:b=json.load(r)
    players=sorted((p for p in b['players'] if p.get('student')),key=lambda p:-p['student_total'])
    player=next(p for p in players if p['player']=='totato2203')
    return dict(at=time.time(),student_rank=players.index(player)+1,student_total=player['student_total'],
                class_shop_all=player.get('student_shop'),class_buyer_all=player.get('student_buyer'),
                seller={k:v for k,v in next(x for x in b['shops'] if x['name']=='totato2203-r3').items() if k not in ('card','owner')},
                buyer={k:v for k,v in next(x for x in b['buyers'] if x['name']=='totato2203-r3').items() if k not in ('owner',)})


def interval(values):
    if not values:return (0,0,0)
    mean=statistics.mean(values)
    if len(values)<2:return mean,None,None
    se=statistics.stdev(values)/math.sqrt(len(values))
    return mean,mean-1.96*se,mean+1.96*se


def review(state):
    start=state['start'];cut=state['cohort_end'];end=state['review_at'];tags=ownership_tags()
    with closing(sqlite3.connect('file:'+(MD/'strategy.sqlite3').as_posix()+'?mode=ro',uri=True,timeout=60)) as c:
        events=[json.loads(r[0]) for r in c.execute("select payload from market_events where ts between ? and ? and kind in ('settle','supply','broken','refund','order','result','storage','expire')",(start,end))]
        c.row_factory=sqlite3.Row
        quotes=[dict(r) for r in c.execute('select * from price_quotes where inquiry=0 and at between ? and ?',(start,cut))]
    with closing(sqlite3.connect('file:'+(MD/'optimization.sqlite3').as_posix()+'?mode=ro',uri=True,timeout=60)) as c:
        unit_details={r[0]:dict(cost=r[1],supplied=r[2]) for r in c.execute('select unit,cost,supplied from units where cost is not null')}
        costs={u:d['cost'] for u,d in unit_details.items()}
    supplies={};refunds={};broken={};sales=[];buys=[]
    for e in events:
        d=e['data'];u=d.get('unit');k=e['kind']
        if k=='supply' and d.get('shop') in tags and e['ts']<=cut:
            for unit in d.get('units',[]):supplies[unit]=d.get('sku')
        if k in ('broken','refund'):
            amount=d.get('refunded',0) if k=='broken' else d.get('amount',0)
            refunds[u]=max(refunds.get(u,0),amount)
            if k=='broken':broken[u]=d
        if k=='settle' and e['ts']<=cut:
            if d.get('shop') in tags:sales.append(e)
            if d.get('buyer')=='totato2203-r3':buys.append(e)
    new_margins=defaultdict(list);known_net=0;known=0
    for e in sales:
        d=e['data'];u=d.get('unit')
        if u not in costs:continue
        net=d['amount']-costs[u]-refunds.get(u,0);known_net+=net;known+=1
        if u in supplies:new_margins[d.get('sku',d.get('item'))].append(net)
    arms=defaultdict(list);cohorts=defaultdict(list);all_arms=defaultdict(list);pending_quotes=0
    for q in quotes:
        horizon=min(6,q.get('warranty',0))*3600+300
        if q['at']+horizon>end or q.get('settled_at') is not None and q['settled_at']+horizon>end:
            pending_quotes+=1;continue
        settled=q.get('settled_at') is not None and q['settled_at']<=end
        net=q['paid']-q['cost']-refunds.get(q['unit'],q.get('refunds') or 0)-(q.get('storage') or 0) if settled else 0
        arms[q['arm']].append(net);cohorts[q['sku'],q['grade'],q['arm']].append(net)
        from score_coordination import all_value
        joint=all_value(read(MD/'score-targets.json',{}),'shop',dict(book=net,revenue=q['paid'] if settled else 0),time.time())
        if joint is not None:all_arms[q['arm']].append(joint)
    current_hashes=hashes()
    drift=[n for n,h in state['hashes'].items() if current_hashes.get(n)!=h]
    changes=[];policy=read(ROOT/'strategy-policy.json',{})
    if not drift and not policy.get('contract_price_learning_enabled'):
        # Only retire clearly loss-making trials; do not call low samples ineffective.
        bad=[]
        for arm,values in arms.items():
            if arm==1.0 or len(values)<30 or interval(values)[2]>=0:continue
            if policy.get('joint_seller_objective_enabled') and all_arms.get(arm) and interval(all_arms[arm])[2]>=0:continue
            positive_specific=any(a==arm and len(v)>=10 and interval(v)[1]>0 for (_,_,a),v in cohorts.items())
            if not positive_specific:bad.append(arm)
        if bad:
            remaining=[a for a in policy['price_multipliers'] if a not in bad]
            policy['seller_margin_share_targets']=[q for q in policy.get('seller_margin_share_targets',[]) if -q not in bad]
            if remaining:
                policy['price_multipliers']=remaining;changes.append(dict(strategy='pricing',action='remove_loss_making_exploration',arms=bad))
                with closing(sqlite3.connect(MD/'strategy.sqlite3',timeout=60)) as c:
                    for (sku,grade,arm),values in cohorts.items():
                        baseline=cohorts.get((sku,grade,1.0),[])
                        if arm in bad and len(baseline)>=10 and interval(baseline)[0]>0:
                            c.execute("update price_state set arm=1.0,at=?,reason='mature-batch-loss' where sku=? and grade=? and arm=?",(time.time(),sku,grade,arm))
                    c.commit()
        excluded=policy.setdefault('procurement_exclusions',{})
        for sku,values in new_margins.items():
            if len(values)>=10 and interval(values)[2]<0:
                excluded[sku]=dict(until=time.time()+21600,reason='mature_new_acquisition_losses',n=len(values),net=sum(values))
                changes.append(dict(strategy='allocation',action='pause_proven_loss_sku_six_hours',sku=sku,n=len(values),net=sum(values)))
        if changes:
            atomic(ROOT/'logs/batch-review-policy-before.json',read(ROOT/'strategy-policy.json',{}));atomic(ROOT/'strategy-policy.json',policy)
    final=board_snapshot();checkpoint=state.get('checkpoint') or read(ROOT/'logs/batch-review-six-hour-checkpoint.json',{})
    feedback=read(MD/'forum-value-feedback.json',{});formats=defaultdict(list)
    for p in feedback.get('posts',{}).values():
        if start<=p.get('ts',0)<=cut:formats[f"{p['role']}:{p['style']}"].append(p)
    timings=[]
    from round3 import lines
    for t in lines(MD/'buyer-timing.jsonl'):
        if start<=t.get('started_at',0)<=cut:timings.append(t)
    durations=sorted(t['elapsed_seconds'] for t in timings)
    calibration=read(MD/'buyer-cohort-calibration.json',{})
    aligned=[r for r in calibration.get('purchases',[]) if start<=r['settled_at']<=cut]
    confirmed=[r for r in aligned if r.get('final_official_reconstructable')]
    previous_gap=state['baseline'].get('student_total',0);hours=max(.001,(cut-start)/3600)
    issued={e['data'].get('order'):e['ts'] for e in events if e['kind']=='order' and e['data'].get('buyer')=='totato2203-r3' and e['ts']<=cut}
    completed={e['data'].get('order'):e['ts'] for e in events if e['kind']=='result' and e['data'].get('buyer')=='totato2203-r3'}
    e2e=sorted(completed[o]-at for o,at in issued.items() if o in completed)
    storage_fee=sum(e['data'].get('fee',0) for e in events if e['kind']=='storage' and e['data'].get('shop') in tags and e['ts']<=cut)
    expiry_cost=sum(e['data'].get('cost',0) for e in events if e['kind']=='expire' and e['data'].get('shop') in tags and e['ts']<=cut)
    turnover=defaultdict(list);window_flows=defaultdict(lambda:dict(bought=0,spend=0,sold=0,revenue=0));sku_nets=defaultdict(float)
    for e in sales:
        d=e['data'];u=d.get('unit');sku=d.get('sku',d.get('item'))
        if u in unit_details:turnover[f"{sku}:{d.get('grade')}"] .append(max(0,e['ts']-unit_details[u]['supplied'])/3600)
        if u in costs:sku_nets[sku]+=d['amount']-costs[u]-refunds.get(u,0)
        w=window_flows[str(int(e['ts']//1200))];w['sold']+=1;w['revenue']+=d['amount']
    for e in events:
        if e['kind']=='supply' and e['data'].get('shop') in tags and e['ts']<=cut:
            w=window_flows[str(int(e['ts']//1200))];w['bought']+=e['data'].get('qty',0);w['spend']+=e['data'].get('total',0)
    base_seller=state['baseline'].get('seller',{});cut_seller=checkpoint.get('seller',{})
    operating_metrics=dict(version=state.get('version'),buyer_processed=len(timings),buyer_median_seconds=statistics.median(durations) if durations else None,
        buyer_p90_seconds=durations[int(.9*(len(durations)-1))] if durations else None,
        buyer_fast_exit_reasons=dict(Counter(t.get('reason') for t in timings)),same_cohort_purchases=len(aligned),
        same_cohort_final_known=len(confirmed),same_cohort_final_score=statistics.mean(r['final_score'] for r in confirmed) if confirmed and len(confirmed)==len(aligned) else None,
        same_cohort_known_subset_mean=statistics.mean(r['final_score'] for r in confirmed) if confirmed else None,
        same_cohort_final_unknown=len(aligned)-len(confirmed),next_order_wait=read(MD/'buyer-opportunity.json',{}).get('next_wait_mean'),
        class_players_gain_per_hour=(final['student_total']-previous_gap)/max(.001,(time.time()-start)/3600),
        seller_revenue_per_hour=sum(e['data']['amount'] for e in sales)/hours,
        seller_known_net_before_fees_per_hour=known_net/hours,
        buyer_public_processed=len(e2e),buyer_public_median_seconds=statistics.median(e2e) if e2e else None,
        buyer_public_p90_seconds=e2e[int(.9*(len(e2e)-1))] if e2e else None,
        buyer_class_all_gain_per_hour=(checkpoint['class_buyer_all']-state['baseline']['class_buyer_all'])/hours if checkpoint.get('class_buyer_all') is not None and state['baseline'].get('class_buyer_all') is not None else None,
        seller_book_growth_per_hour=(cut_seller['book']-base_seller['book'])/hours if 'book' in cut_seller and 'book' in base_seller else None,
        seller_official_revenue_growth_per_hour=(cut_seller['revenue']-base_seller['revenue'])/hours if 'revenue' in cut_seller and 'revenue' in base_seller else None,
        seller_cash_change_per_hour=(cut_seller['profit']-base_seller['profit'])/hours if 'profit' in cut_seller and 'profit' in base_seller else None,
        seller_partial_net_after_storage_expiry_per_hour=(known_net-storage_fee-expiry_cost)/hours,
        seller_mean_settled_price=statistics.mean(e['data']['amount'] for e in sales) if sales else None,
        seller_quote_settlement_rate=sum(q.get('paid') is not None for q in quotes)/len(quotes) if quotes else None,
        seller_turnover_hours={k:dict(n=len(v),mean=statistics.mean(v),median=statistics.median(v)) for k,v in turnover.items()},
        seller_top_absolute_known_net=sorted([dict(sku=k,known_net=v) for k,v in sku_nets.items()],key=lambda x:-x['known_net'])[:15],supply_window_flows=dict(window_flows),
        seller_class_all_gain_per_hour=(checkpoint['class_shop_all']-state['baseline']['class_shop_all'])/hours if checkpoint.get('class_shop_all') is not None and state['baseline'].get('class_shop_all') is not None else None,
        buyer_target_median=15,buyer_target_p90=40,throughput_target_multiplier=1.5,
        unavailable_metrics=['final use where not public','counterfactual good trades lost to fast exit'],
        rollback_rules=['receipt/signature/budget violation: restore buyer path immediately','seller/supply failure: restore offending path only',
                        'lower buyer throughput without score improvement: revise timing policy','book loss without rank benefit: stop the specific price/buy arm'])
    result=dict(at=time.time(),start=start,cohort_end=cut,matured_through=end,baseline=state['baseline'],six_hour_checkpoint=checkpoint,final=final,
                operating_metrics=operating_metrics,pending_quote_outcomes=pending_quotes,
                configuration_drift=drift,changes=changes,sales=len(sales),known_cost_sales=known,known_net_before_storage_expiry=known_net,
                new_acquired_units=len(supplies),new_acquisition_sold=sum(e['data'].get('unit') in supplies for e in sales),
                new_acquisition_margins={k:dict(n=len(v),net=sum(v),mean=interval(v)[0],lower=interval(v)[1],upper=interval(v)[2]) for k,v in new_margins.items()},
                buyer=dict(deals=len(buys),grades=dict(Counter(e['data'].get('grade') for e in buys)),broken=sum(e['data'].get('unit') in broken for e in buys),refunded=sum(refunds.get(e['data'].get('unit'),0)>0 for e in buys)),
                price_arms={str(k):dict(opportunities=len(v),net=sum(v),mean=interval(v)[0],lower=interval(v)[1],upper=interval(v)[2]) for k,v in arms.items()},
                joint_price_arms={str(k):dict(n=len(v),mean=interval(v)[0],lower=interval(v)[1],upper=interval(v)[2]) for k,v in all_arms.items()},
                forum_formats={k:dict(posts=len(v),likes=sum(x.get('likes',0) or 0 for x in v),dislikes=sum(x.get('dislikes',0) or 0 for x in v)) for k,v in formats.items()},
                limitations=['동시 적용이므로 개별 전략의 인과효과를 분리하지 못한다.','순기여는 확인 원가·환불 기준이며 보관료·폐기를 제외한다.','구매 고장은 사용 실패 점수와 동일하다고 단정하지 않는다.','설정 변경이 발견되면 자동 정책 변경을 하지 않는다.'])
    atomic(ROOT/'logs/strategy-batch-review-result.json',result)
    rows=['# 전략 일괄 실효성 검증',f"운영 표본: {stamp(start)} ~ {stamp(cut)}",f"성숙 결과 확인: {stamp(end)}",'',
          f"학생 순위 {state['baseline']['student_rank']} → {final['student_rank']}; 성숙 판매 {len(sales)}건, 원가 확인 {known}건.",
          f"확인 순기여 {known_net:,.0f} credits(보관료·폐기 제외).",f"설정 변경: {', '.join(drift) or '없음'}",'',
          '| 전략 | 제안 이유 | 확인 지표 | 판정 |','|---|---|---|---|']
    for key,name,reason,metric in STRATEGIES:
        relevant=[c for c in changes if c['strategy']==key]
        verdict='근거에 따른 수정 적용: '+json.dumps(relevant,ensure_ascii=False) if relevant else '표본·수익·노출을 종합 검토할 자료 확보. 독립 효과는 분리 불가.'
        if key=='retired':verdict='기존 폐기 상태 유지'
        rows.append(f'| {name} | {reason} | {metric} | {verdict} |')
    rows+=['','## 실제 결과','```json',json.dumps({k:v for k,v in result.items() if k not in ('baseline','final','six_hour_checkpoint')},ensure_ascii=False,indent=2),'```']
    (ROOT/'STRATEGY-BATCH-REVIEW.md').write_text('\n'.join(rows),encoding='utf-8')
    return result


def wait_until(deadline):
    while time.time()<deadline:time.sleep(min(60,max(0,deadline-time.time())))


def tick_one(now=None,state_path=None,repeat=True):
    """Reuse the permanent worker: one combined review, no extra daemon/model."""
    now=time.time() if now is None else now
    path=STATE if state_path is None else Path(state_path)
    state=read(path,{})
    if state.get('status') in ('scheduled','maturing') and state.get('controller')!='existing_market_worker':
        return {'status':'standalone_review_owns_cohort'}
    if state.get('controller')=='existing_market_worker' and state.get('pid')!=os.getpid():
        state['pid']=os.getpid();atomic(path,state)
    costly=state.get('status') not in ('scheduled','maturing') or now>=state.get('review_at',float('inf')) or state.get('status')=='scheduled' and now>=state['cohort_end']
    if costly:
        attempt=MD/(path.stem+'-attempt.json')
        if 0<=now-read(attempt,{}).get('at',-float('inf'))<900:return {'status':'review_retry_backoff'}
        atomic(attempt,{'at':now})
    if state.get('status') not in ('scheduled','maturing'):
        if not repeat:return {'status':'predecessor_complete'}
        if state:
            atomic(ROOT/f"logs/batch-review-archive-{int(state.get('start',now))}.json",state)
            previous=read(ROOT/'logs/strategy-batch-review-result.json',{})
            if previous:atomic(ROOT/f"logs/batch-result-archive-{int(state.get('start',now))}.json",previous)
        state=dict(start=now,cohort_end=now+21600,review_at=now+43200,hashes=hashes(),baseline=board_snapshot(),status='scheduled',controller='existing_market_worker',pid=os.getpid(),version=read(ROOT/'strategy-policy.json',{}).get('version'))
        atomic(path,state)
        return {'status':'new_joint_cohort','review_kst':stamp(state['review_at'])}
    if state['status']=='scheduled' and now>=state['cohort_end']:
        checkpoint=board_snapshot();atomic(ROOT/'logs/batch-review-six-hour-checkpoint.json',checkpoint);state['checkpoint']=checkpoint;state['checkpoint_inventory']=read(MD/'inventory-plan.json',{});state['status']='maturing';atomic(path,state)
        if state.get('version')=='rank-velocity-v10.1':
            atomic(ROOT/'logs/v101-operating-checkpoint.json',dict(at=now,state_version=state['version'],start=state['start'],cohort_end=state['cohort_end'],official=checkpoint,
                accounting=read(MD/'buyer-v101-accounting.json',{}),buyer_stages=read(MD/'buyer-stage-diagnostics.json',{}),procurement=read(MD/'procurement-preview.json',{}),
                price_contracts=read(MD/'price-hierarchy.json',{}).get('contract_funnels',{}),interpretation='90-minute operations checkpoint; warranty results pending'))
    if now>=state['review_at']:
        result=review(state);state.update(status='complete',completed_at=now,applied_changes=result['changes']);atomic(path,state)
        atomic(ROOT/f"logs/strategy-review-{state.get('version','unknown')}-{int(state['start'])}.json",result)
        return {'status':'joint_review_complete','changes':result['changes']}
    return {'status':state['status']}


def tick(now=None):
    v101_path=MD/'strategy-v101-review.json'
    if v101_path.exists():
        v10=MD/'strategy-v9-review.json'
        predecessor=tick_one(now,v10,repeat=False) if v10.exists() else {}
        result=tick_one(now,v101_path,repeat=False);result['v10_status']=predecessor.get('status')
        return result
    v9_path=MD/'strategy-v9-review.json'
    if v9_path.exists():
        return tick_one(now,v9_path,repeat=False)
    new_path=MD/'strategy-v8-review.json'
    if new_path.exists():
        predecessor=tick_one(now,STATE,repeat=False)
        if read(new_path,{}).get('status')=='awaiting_activation':return dict(status='awaiting_activation',predecessor_status=predecessor['status'])
        result=tick_one(now,new_path)
        result['predecessor_status']=predecessor['status']
        return result
    return tick_one(now)


def main():
    os.chdir(ROOT);lock=(MD/'strategy-batch-review.lock').open('a+b');lock.seek(0)
    if lock.read(1)==b'':lock.write(b'0');lock.flush()
    lock.seek(0)
    if os.name=='nt':
        import msvcrt
        try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        except OSError:print('batch_review_already_running',flush=True);return
    state=read(STATE,{})
    if state.get('status') not in ('scheduled','maturing'):
        start=time.time();state=dict(start=start,cohort_end=start+21600,review_at=start+43200,hashes=hashes(),baseline=board_snapshot(),status='scheduled')
    state.update(pid=os.getpid(),review_kst=stamp(state['review_at']));atomic(STATE,state)
    print(json.dumps(dict(status=state['status'],cohort_end=stamp(state['cohort_end']),review_at=state['review_kst'])),flush=True)
    if state['status']=='scheduled':
        wait_until(state['cohort_end']);atomic(ROOT/'logs/batch-review-six-hour-checkpoint.json',board_snapshot())
        state['status']='maturing';atomic(STATE,state)
    wait_until(state['review_at'])
    try:
        result=review(state);state.update(status='complete',completed_at=time.time(),applied_changes=result['changes'])
    except Exception as exc:state.update(status='failed',error=type(exc).__name__+': '+str(exc))
    atomic(STATE,state);print(json.dumps(state.get('status')),flush=True)


if __name__=='__main__':main()
