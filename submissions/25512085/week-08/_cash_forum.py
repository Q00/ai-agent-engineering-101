"""Internal availability-only notice planning; publishing remains with callers."""


def _select_refs(hub, skus, limit=5):
    """Collect up to `limit` unique integer ledger seqs backing announced SKUs/market activity."""
    refs, seen = [], set()
    target = set(skus)

    def add_seq(val):
        try:
            seq = int(val)
        except (TypeError, ValueError):
            return False
        if seq > 0 and seq not in seen:
            seen.add(seq)
            refs.append(seq)
        return len(refs) >= limit

    sc = hub.get('seller_cash') or {}
    sales = sc.get('sales') or {}
    own_rows = sorted(sales.items(), key=lambda kv: (kv[1] or {}).get('ts', 0), reverse=True)
    for seq, row in own_rows:
        if (row or {}).get('sku') in target and add_seq(seq):
            return refs
    msales = sc.get('market_sales') or {}
    mkt_rows = sorted(
        ((ts, seq) for sku in skus for seq, ts in (msales.get(sku) or {}).items()),
        reverse=True,
    )
    for _, seq in mkt_rows:
        if add_seq(seq):
            return refs
    for seq, _ in own_rows:
        if add_seq(seq):
            return refs
    for seq in reversed((hub.get('demand_radar') or {}).get('recent_refs') or []):
        if add_seq(seq):
            return refs
    return refs


def availability_plan(policy, units, hub, catalog, now, forum=None):
    """Match real recent requests to certified stock; publish availability only."""
    state=hub.get('forum_state',{})
    if now-state.get('last_shop_post_ts',0)<300:
        return None
    held={u['sku']:u for u in units if u.get('sku') in catalog and u.get('cert')
          and u.get('grade')!='expired' and (u.get('kind')!='food' or (u.get('expires_at') or 0)>now+600)}
    if not held:
        return None
    lookup={s.lower():s for s in held}
    def text_for(skus, refs):
        lines=['[mjforge 재고 안내]']
        for sku in skus[:3]:
            name=str(catalog[sku].get('name') or held[sku].get('name') or sku).replace('\n',' ').replace('\r',' ')[:100]
            grade=held[sku].get('grade')
            lp=catalog[sku].get('list_price') or 0
            meta=[]
            if grade:
                meta.append(f'인증 {grade}등급')
            if isinstance(lp,(int,float)) and lp>0:
                meta.append(f'정가 {int(lp):,}')
            suffix=f" ({', '.join(meta)})" if meta else ''
            lines.append(f'{sku} {name}: 재고 있음{suffix}')
        if refs:
            lines.append('최근 원장 정산 근거 refs 첨부 (관측된 정산 기록이며 향후 재고/가격 보장은 아님)')
        return '\n'.join(lines)
    posts=forum.get('posts',[]) if isinstance(forum,dict) else forum if isinstance(forum,list) else []
    replied=set(state.get('availability_replied_threads',[]))
    request=policy.re.compile(r'looking for|want to buy|stock request|(?:check\s+)?availability\s*(?:of|\?)|in stock\?|구매\s*(?:희망|원|요청)|재고\s*(?:있|문의)|찾(?:아요|습니다|고 있어)',policy.re.I)
    report=policy.re.compile(r'\bsettled\b|\bpaid\b|\bpurchased\b|\bbought\b|not a guarantee|정산|구매\s*(?:완료|후기)|거래\s*(?:완료|후기)|리포트',policy.re.I)
    for post in sorted(posts,key=lambda x:x.get('ts',0),reverse=True):
        pid=post.get('id');body=str(post.get('text') or '')
        if (post.get('role')!='buyer' or str(post.get('owner') or '').lower()=='mjforge'
                or not policy.number(post.get('ts')) or not 0<=now-post['ts']<=1200
                or pid is None or str(pid) in replied or report.search(body) or not request.search(body)):
            continue
        skus=sorted({lookup[k.lower()] for k in policy.re.findall(r'\b[GgFf][0-9a-fA-F]{7,10}\b',body) if k.lower() in lookup})
        if skus:
            refs=_select_refs(hub, skus[:3])
            return {'action':{'post':text_for(skus, refs),'refs':refs,'as':'shop','thread':pid},
                    'skus':skus[:3],'demand_ts':post['ts'],'thread':str(pid)}
    if now-state.get('last_shop_post_ts',0)<policy.config().get('forum_interval_seconds',1200):
        return None
    announced=state.get('availability_announced',{})
    wants=hub.get('demand_radar',{}).get('sku_inquiries',{})
    ranked=[]
    for sku in held:
        events=[e['ts'] for e in wants.get(sku.lower(),[]) if policy.number(e.get('ts')) and 0<=now-e['ts']<=1200
                and e['ts']>announced.get(sku,0)]
        if events:ranked.append((len(events),max(events),sku))
    if not ranked:
        return None
    chosen=sorted(ranked,reverse=True)[:3];skus=[v[2] for v in chosen]
    refs=_select_refs(hub, skus)
    return {'action':{'post':text_for(skus, refs),'refs':refs,'as':'shop'},'skus':skus,
            'demand_ts':max(v[1] for v in chosen),'thread':None}
