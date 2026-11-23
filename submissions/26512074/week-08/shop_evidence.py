"""Public Round 3 evidence, with bounded interpretations and no model calls."""
from collections import Counter,defaultdict
import math
from shop_profit import ownership_tags


def analyze(board, shops, ledger, now,observed_quotes=(),include_leaders=True):
    if board.get('round') != 3:
        return {'leaders': [], 'products': [], 'repeat_pairs': []}
    def number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    ranked = sorted((s for s in board.get('shops', []) if number(s.get('all'))),
                    key=lambda s: (-s['all'], s['name']))[:5]
    identities = {s['name']: {s['name'], s.get('url')} for s in shops}
    for quote in observed_quotes:
        if quote.get('shop') in identities and quote.get('url'):
            identities[quote['shop']].add(quote['url'])
    for s in ranked:
        identities.setdefault(s['name'], {s['name']}).add((s.get('card') or {}).get('url'))
    owners = {alias: name for name, aliases in identities.items() for alias in aliases if alias}
    ours=set(ownership_tags())|{'totato2203'}
    for shop in board.get('shops',[]):
        if shop.get('name') in ours:
            ours.add((shop.get('owner') or {}).get('login'))
    events, seen = [], set()
    for event in ledger:
        seq, ts = event.get('seq'), event.get('ts')
        data = event.get('data', {})
        if (event.get('kind') != 'settle' or event.get('round') != 3 or seq is None
                or seq in seen or not number(ts) or not 0 <= now-ts < 86400
                or not number(data.get('amount')) or data['amount'] <= 0):
            continue
        seen.add(seq)
        events.append(event)
    pairs = Counter((e['data'].get('buyer'), owners.get(e['data'].get('shop')))
                    for e in events)
    totals = Counter(e['data'].get('buyer') for e in events)
    pair_items=defaultdict(Counter)
    pair_amounts=Counter()
    pair_refs=defaultdict(list)
    names={}
    for event in events:
        data=event['data'];key=(data.get('buyer'),owners.get(data.get('shop')))
        sku=data.get('sku') or data.get('item')
        if sku:
            pair_items[key][sku]+=1
            names[sku]=data.get('name') or sku
        pair_amounts[key]+=data['amount']
        pair_refs[key].append(event['seq'])
    repeats = [{'buyer': b, 'shop': s, 'count': count,
                'buyer_total': totals[b], 'share': count/totals[b], 'amount': pair_amounts[b,s],
                'reverse_count': pairs.get((s, b), 0),
                'products': [{'sku':sku,'name':names[sku],'count':n} for sku,n in pair_items[b,s].most_common(3)],
                'refs': pair_refs[b,s][-2:],
                'buyer_matches_shop_name': b in identities,
                'buyer_shop_identity_verified': False}
               for (b, s), count in pairs.most_common() if b and s and count >= 3
               and b not in ours and s not in ours]
    price_groups=defaultdict(list)
    for event in events:
        data=event['data']
        price_groups[data.get('sku') or data.get('item'),data.get('grade')].append(event)
    leaders, products = [], []
    for rank, shop in enumerate(ranked, 1):
        name = shop['name']
        observed = [e for e in events if owners.get(e['data'].get('shop')) == name]
        counts = Counter(e['data'].get('sku') or e['data'].get('item') for e in observed)
        sku = counts.most_common(1)
        if sku:
            item, count = sku[0]
            matching = [e for e in observed if (e['data'].get('sku') or e['data'].get('item')) == item]
            prices = [e['data']['amount'] for e in matching]
            products.append({'shop': name, 'sku': item, 'count': count,
                             'low': min(prices), 'high': max(prices), 'revenue': sum(prices),
                             'refs': [e['seq'] for e in matching[-2:]]})
        if rank > 3 or not include_leaders:
            continue
        facts = []
        for field, label in [('refunds', '환불'), ('broken', '고장')]:
            value = shop.get(field)
            if number(value) and value > 0:
                facts.append(f'누적 {label} {value:,.0f}건')
        claims, honest = shop.get('claims'), shop.get('honest_claims')
        if number(claims) and number(honest) and 0 <= honest < claims:
            facts.append(f'누적 등급 주장 중 honest_claims와 차이 {claims-honest:,.0f}건')
        buyers = Counter(e['data'].get('buyer') for e in observed if e['data'].get('buyer'))
        if buyers and len(observed) >= 3:
            _, count = buyers.most_common(1)[0]
            if count / len(observed) >= .5:
                facts.append(f'24시간 관측 단일 구매자 집중 {count/len(observed):.1%}({count}/{len(observed)}건)')
        if sku and len(observed)>=10 and sku[0][1]/len(observed)>=.5:
            facts.append(f'24시간 관측 단일 SKU 집중 {sku[0][1]/len(observed):.1%}')
        short_life=[e for e in observed if number(e['data'].get('expires_at'))
                    and 0<e['data']['expires_at']-e['ts']<=21600]
        if short_life:
            facts.append(f'24시간 관측 정산 당시 잔여수명 6시간 이하 {len(short_life)}건')
        mismatches=sum(e['data'].get('claim_honest') is False for e in observed)
        if mismatches:facts.append(f'24시간 관측 등급 주장 불일치 {mismatches}건')
        # Compare realized prices only against matching grades, never mixed-grade averages.
        from statistics import median
        expensive=0;matched=0
        for event in observed:
            data=event['data'];item=data.get('sku') or data.get('item');grade=data.get('grade')
            if grade is None:continue
            peers=[e['data']['amount'] for e in price_groups[item,grade] if owners.get(e['data'].get('shop'))!=name
                   and 0<=event['ts']-e['ts']<7200]
            if len(peers)>=3:
                matched+=1
                expensive+=data['amount']>median(peers)*1.15
        if expensive>=3:
            facts.append(f'동일 SKU·등급 비교 {matched}건 중 타점 중앙값보다 15% 초과 {expensive}건(보증 차이 미조정)')
        leaders.append({'rank': rank, 'shop': name, 'all': shop['all'], 'facts': facts,
                        'refs': [e['seq'] for e in observed[-2:]]})
    return {'leaders': leaders, 'products': products, 'repeat_pairs': repeats}


def render(evidence, now):
    rows = ['Top 3 점검 | Round 3 공개 보드 ALL 내림차순(동점은 이름순)']
    refs = []
    rotation = int(now // 300)
    for leader in evidence['leaders']:
        facts = leader['facts']
        fact = ' / '.join(facts) if facts else '현재 수집 자료에서 추가로 지적할 검증된 부정적 사실 없음.'
        rows.append(f"{leader['rank']}. {leader['shop']} (ALL {leader['all']:g}): {fact}")
        refs.extend(leader['refs'])
    if not evidence['leaders']:
        rows.append('순위 자료 확인 실패: 이전 순위를 현재 순위로 게시하지 않습니다.')
    rows.append('Top 5 주력 상품 | 최근 24시간 수집 정산에서 가게별 최다 판매 SKU')
    for product in evidence['products']:
        rows.append(f"{product['shop']}: {product['sku']} {product['count']}건 · 정산 매출 {product.get('revenue',0):,.0f} credits · 정산가 {product['low']:,.0f}~{product['high']:,.0f} credits.")
        refs.extend(product['refs'])
    rows.append('반복 거래 관측 | 최근 24시간 수집 정산, 동일 구매자→판매처 3건 이상 중 상위 3쌍')
    rows.append('담합해서 물건을 특정 에이전트끼리 대량으로 거래하는 가게는 거래를 끊어서 견제해야 합니다')
    pairs = evidence.get('repeat_pairs', [])[:3]
    for pair in pairs:
        rows.append(f"구매자 {pair['buyer']} → 판매처 {pair['shop']}: {pair['count']}건, "
                    f"해당 구매자 관측 거래 {pair['buyer_total']}건 중 {pair['share']:.1%}. "
                    f"정산 총액 {pair.get('amount',0):,.0f} credits. "
                    f"역방향 이름 일치 거래 {pair['reverse_count']}건.")
        rows.append('구매 상품: '+', '.join(f"{p['sku']} {p['name'][:20]} {p['count']}건" for p in pair.get('products',[])))
        refs.extend(pair['refs'])
    if pairs:
        perspectives = [
            '해석: 반복 구매 선호나 안정적인 공급 관계일 가능성이 있습니다. 거래 조건과 대체 판매처 견적을 비교할 만합니다.',
            '해석: 거래 상대가 편중됐을 가능성이 있습니다. 가격 경쟁력·상품 구성·재고 가용성도 가능한 설명입니다.',
            '해석: 특정 상대에 대한 의존 가능성을 살펴볼 수 있습니다. 관측 기간이 짧거나 수집이 제한된 영향일 수도 있습니다.']
        rows.append(perspectives[rotation % len(perspectives)])
    else:
        rows.append('조건을 충족하는 반복 거래 쌍이 관측되지 않았습니다.')
    return rows, refs
