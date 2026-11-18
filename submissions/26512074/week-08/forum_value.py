"""Short, matched settlement reports for both forum roles; no engagement asks."""
import json
import sqlite3
import time
import math
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from statistics import median
from optimizer import read,atomic
from shop_profit import ownership_tags


def enabled(md):
    md=Path(md);root=Path(__file__).resolve().parent
    if (md/'forum-policy.json').exists():return bool(read(md/'forum-policy.json',{}).get('enabled'))
    return md.resolve()==(root/'.market').resolve() and bool(read(root/'forum-policy.json',{}).get('enabled'))


def build(md,role,now=None):
    md=Path(md);now=time.time() if now is None else now
    path=md/'strategy.sqlite3'
    if not path.exists():return None
    with closing(sqlite3.connect('file:'+path.resolve().as_posix()+'?mode=ro',uri=True,timeout=5)) as c:
        rows=c.execute("select payload from market_events where kind='settle' and ts between ? and ? order by ts desc,seq desc limit 10000",(now-7200,now)).fetchall()
    events=[];seen=set()
    for row in rows:
        e=json.loads(row[0]);d=e.get('data',{})
        if e.get('round',3)!=3 or e['seq'] in seen or not isinstance(d.get('amount'),(int,float)) or isinstance(d['amount'],bool) or d['amount']<=0:continue
        seen.add(e['seq']);events.append(e)
    state=read(md/'forum-value-state.json',{});used=set(state.get(role,{}).get('used',[]));tags=ownership_tags()
    own=[e for e in events if (e['data'].get('shop') in tags if role=='shop' else e['data'].get('buyer')=='totato2203-r3') and e['seq'] not in used]
    if not own:return None
    catalog=read(md/'catalog.json',{});shops=read(md/'shops.json',[])
    if isinstance(shops,dict):shops=shops.get('shops',[])
    names={s.get('url'):s.get('name') for s in shops}
    groups=defaultdict(list)
    for e in events:
        d=e['data'];groups[d.get('sku',d.get('item')),d.get('grade'),d.get('warranty_hours') or 0].append(e)
    turn=state.get(role,{}).get('count',0);style=('purchase','comparison','shelf_life')[turn%3]
    formats=read(md/'forum-value-feedback.json',{}).get('formats',{})
    mature={k.split(':',1)[1]:v for k,v in formats.items() if k.startswith(role+':') and v.get('posts',0)>=20 and not k.endswith(':legacy')}
    if len(mature)>=3 and turn%4!=0:
        measurable={k:v for k,v in mature.items() if v.get('all_observations',0)>0}
        if measurable:style=max(measurable,key=lambda k:measurable[k].get('mean_all_proxy',0))
    def cohort(e):
        d=e['data'];peers=groups[d.get('sku',d.get('item')),d.get('grade'),d.get('warranty_hours') or 0]
        return [p for p in peers if p['seq']!=e['seq']]
    if style=='shelf_life':
        candidates=[e for e in own if isinstance(e['data'].get('expires_at'),(int,float))] or own
    elif style=='comparison':candidates=[e for e in own if len(cohort(e))>=3] or own
    else:candidates=own
    # Prefer new topics and comparable evidence over repeating the latest SKU.
    topics=state.get(role,{}).get('topics',{})
    candidates.sort(key=lambda e:(now-topics.get(e['data'].get('sku',e['data'].get('item')),0)>=3600,
                                  len(cohort(e))>=3,e['ts']),reverse=True)
    e=candidates[0];d=e['data'];sku=d.get('sku',d.get('item'));product=catalog.get(sku,{});price=d['amount'];hours=d.get('warranty_hours') or 0
    peers=cohort(e);label='실제 구매 기록' if role=='buyer' else '실제 판매 기록'
    title={'purchase':label,'comparison':'동일 조건 체결가 비교','shelf_life':'정산 당시 유통기한 기록'}[style]
    if style=='shelf_life' and not isinstance(d.get('expires_at'),(int,float)):title=label
    rows=[f"[{title}] {product.get('name',d.get('name',sku))[:70]} [{sku}]",
          f"실결제 {price:,.0f} credits · 등급 {d.get('grade') or '미공개'} · 보증 {hours:g}h"]
    listed=product.get('list_price')
    if listed:rows.append(f"정가 {listed:,.0f} 대비 {(1-price/listed)*100:.1f}% 할인. 정가 할인율이며 Saved 점수와는 다릅니다.")
    if role=='buyer' and names.get(d.get('shop')):rows.append('판매처: '+names[d['shop']])
    refs=[e['seq']]
    if len(peers)>=3:
        benchmark=median(p['data']['amount'] for p in peers)
        rows.append(f"최근 2h 동일 SKU·등급·보증 정산 {len(peers)}건 중앙값 {benchmark:,.0f}; 이 거래는 중앙값 대비 {(price/benchmark-1)*100:+.1f}%.")
        refs.extend(p['seq'] for p in peers[:3])
    if isinstance(d.get('expires_at'),(int,float)):
        rows.append(f"정산 당시 남은 유통기한 {max(0,(d['expires_at']-e['ts'])/3600):.1f}h. 사용 시각에 맞는 잔여수명을 별도 확인해야 합니다.")
        if len(peers)>=3:rows.append('비교 가격은 잔여수명 차이를 조정하지 않은 관측값입니다.')
    if d.get('claim_honest') is True:rows.append('원장에서 등급 주장 일치 확인. 이후 사용 결과·고장 여부는 별도입니다.')
    rows.extend([f"검증 원장 #{e['seq']} · 과거 정산 기록이며 현재 호가·재고를 뜻하지 않습니다.",
                 'https://market.ourolabs.ai/api/ledger?after='+str(e['seq']-1)])
    return dict(post='\n'.join(rows),refs=refs,**{'as':role},_style=style,_seq=e['seq'])


def measure(md,now=None):
    md=Path(md);now=time.time() if now is None else now
    state=read(md/'forum-value-feedback.json',dict(posts={}))
    if 0<=now-state.get('at',0)<30:return state
    posts=state.setdefault('posts',{})
    from strategy_policy import load
    repair=load(md).get('forum_observation_repair_enabled',False)
    board=read(md/'patch36-scoreboard.json',{});author=board.get('buyer',{})
    author_rating=author.get('forum');author_posts=author.get('posts')
    from score_coordination import progress
    post_component=read(md/'score-targets.json',{}).get('components',{}).get('buyer',{}).get('posts',{})
    post_percentile=progress(post_component.get('values',[]),author_posts) if author_posts is not None and post_component.get('values') else None
    for p in read(md/'forum.json',[]):
        if p.get('author')!='totato2203-r3':continue
        old=posts.get(str(p['id']),{})
        posts[str(p['id'])]=dict(role=p.get('role'),ts=p.get('ts'),likes=p.get('likes',0),dislikes=p.get('dislikes',0),score=p.get('score'),
                                last_seen_at=now,style=next((s for s,t in [('comparison','동일 조건'),('shelf_life','유통기한 기록'),('purchase','실제 구매 기록'),('purchase','실제 판매 기록')] if t in p.get('text','')[:40]),old.get('style','legacy')))
        current=posts[str(p['id'])]
        if repair:
            for field in ('all_proxy','rating_delta','posts_delta','maturity_observed_at','attribution'):
                if field in old:current[field]=old[field]
        current['initial_author_forum']=old.get('initial_author_forum',author_rating)
        current['initial_author_posts']=old.get('initial_author_posts',author_posts)
        current['initial_post_percentile']=old.get('initial_post_percentile',post_percentile)
        if repair:
            if current['initial_author_forum'] is None:current['initial_author_forum']=author_rating
            if current['initial_post_percentile'] is None:current['initial_post_percentile']=post_percentile
            current['initial_author_observed_at']=old.get('initial_author_observed_at',p.get('ts',now)) if old.get('initial_author_forum') is not None else now
        current['hour_kst']=int((p.get('ts',now)+9*3600)//3600)%24
        current['unique_eligible_voters']=old.get('unique_eligible_voters')
        current['stance_changes']=old.get('stance_changes')
        current['voter_identity_source']='not_exposed_by_public_forum_api'
        votes=p.get('votes')
        if isinstance(votes,list):
            qualified={str(v.get('voter')):v for v in votes if v.get('voter') and v.get('settled_deals',0)>=50 and v.get('owner')!='totato2203'}
            current['unique_eligible_voters']=len(qualified)
            current['stance_changes']=sum(v.get('previous_value') is not None and v.get('value')!=v.get('previous_value') for v in qualified.values())
            current['stance_log_weight']=sum(1+math.log2(max(1,v.get('net_likes',1))) if v.get('value',0)>0 else -1 for v in qualified.values())
            current['voter_identity_source']='explicit_api_vote_records'
        if not repair and now-p.get('ts',now)>=1500 and author_rating is not None and current['initial_author_forum'] is not None:
            rating_delta=author_rating-current['initial_author_forum']
            post_delta=max(0,(author_posts or 0)-(current['initial_author_posts'] or 0))
            initial_pct=current.get('initial_post_percentile')
            post_gain=.1*(post_percentile-initial_pct) if post_percentile is not None and initial_pct is not None else 0
            current['all_proxy']=.3*rating_delta+post_gain
            current['rating_delta']=rating_delta;current['posts_delta']=post_delta
            current['attribution']='overlapping intervals; observational, not causal'
    if repair:
        mature_stored_posts(posts,author_rating,author_posts,post_percentile,now)
    groups=defaultdict(list)
    for p in posts.values():
        # Public peer implementations observe likes arriving for about 25 min.
        # Do not pick a champion from an immature five-minute count.
        if 1500<=now-p.get('ts',now)<86400:groups[f"{p['role']}:{p['style']}"].append(p)
    state.update(at=now,objective='observed_rating_and_class_posts_marginal_proxy',formats={k:dict(posts=len(v),mean_likes=sum(p['likes'] for p in v)/len(v),mean_dislikes=sum(p['dislikes'] for p in v)/len(v),
        all_observations=sum('all_proxy' in p for p in v),mean_all_proxy=sum(p.get('all_proxy',0) for p in v)/max(1,sum('all_proxy' in p for p in v))) for k,v in groups.items()})
    buckets=defaultdict(list)
    for values in groups.values():
        for p in values:
            if 'all_proxy' in p:buckets[f"{p['role']}:{p['style']}:{p.get('hour_kst',0)}"].append(p['all_proxy'])
    state['time_buckets']={k:dict(n=len(v),mean_all_proxy=sum(v)/len(v)) for k,v in buckets.items()}
    atomic(md/'forum-value-feedback.json',state)
    return state


def mature_stored_posts(posts,author_rating,author_posts,post_percentile,now):
    """Mature stored observations even after posts leave the recent API page."""
    for current in posts.values():
        if current.get('role')!='buyer':
            current['direct_buyer_all_eligible']=False
            continue
        baseline=current.get('initial_author_forum');initial=current.get('initial_post_percentile')
        age=now-current.get('initial_author_observed_at',current.get('ts',now))
        if 'all_proxy' in current or baseline is None or author_rating is None or not 1500<=age<3600:continue
        rating_delta=author_rating-baseline
        post_gain=.1*(post_percentile-initial) if post_percentile is not None and initial is not None else 0
        current.update(all_proxy=.3*rating_delta+post_gain,rating_delta=rating_delta,
            posts_delta=max(0,(author_posts or 0)-(current.get('initial_author_posts') or 0)),
            maturity_observed_at=now,direct_buyer_all_eligible=True,
            attribution='overlapping intervals; observational, not causal; frozen at first mature observation')


def publish(md,role,enabled=True):
    from forum_schedule import due,queued
    md=Path(md);now=time.time();measure(md,now)
    if not due(md,role,now):return {'status':'staggered_interval'}
    box=md/'outbox.jsonl'
    if box.exists():
        for line in box.read_text(encoding='utf-8').splitlines():
            try:r=json.loads(line)
            except ValueError:continue
            if r.get('as')==role and r.get('post'):return {'status':'pending_outbox'}
    post=build(md,role,now)
    if post is None:return {'status':'awaiting_new_own_settlement'}
    style=post.pop('_style');seq=post.pop('_seq')
    if not enabled:return dict(status='preview',**post)
    assert len(post['post'])<=2000 and post['refs']
    with box.open('a',encoding='utf-8') as f:f.write(json.dumps(post,ensure_ascii=False)+'\n')
    state=read(md/'forum-value-state.json',{});prior=state.get(role,{})
    topics=prior.get('topics',{})
    # _seq is an actual event; resolve its SKU without publishing private state.
    with closing(sqlite3.connect(md/'strategy.sqlite3',timeout=5)) as c:
        row=c.execute('select payload from market_events where seq=?',(seq,)).fetchone()
    if row:
        data=json.loads(row[0])['data'];topics[data.get('sku',data.get('item'))]=now
    state[role]=dict(at=now,count=prior.get('count',0)+1,used=(prior.get('used',[])+[seq])[-1000:],last_style=style,topics={k:v for k,v in topics.items() if now-v<86400})
    atomic(md/'forum-value-state.json',state);queued(md,role,now)
    return dict(status='queued',role=role,style=style,refs=post['refs'],characters=len(post['post']))
