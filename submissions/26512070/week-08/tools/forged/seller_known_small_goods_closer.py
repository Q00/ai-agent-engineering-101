"""For known buyers on goods under 30k list in turns 1-3 (no offer yet, or a lowball offer under 0.8 of our ask): opens nearer the floor, accepts any offer above floor plus 25% of the floor-to-list margi

Forged 2026-11-13 03:06 by harness.forge for states ['goods|<30k|1|none|True', 'goods|<30k|2|<0.8|True', 'goods|<30k|3|<0.8|True']."""

def decide(v):
    if v.get('kind') != 'goods' or not v.get('known'):
        return None
    lp = v.get('list_price') or 0
    if lp <= 0 or lp >= 30000:
        return None
    turn = v.get('turn') or 1
    floor = max(v.get('floor') or 0, v.get('cost') or 0)
    if floor >= lp:
        return None
    offer = v.get('offer')
    ask = v.get('ask')
    margin = lp - floor
    if turn == 1:
        if offer is not None:
            return None
        price = int(round(floor + 0.6 * margin))
        price = min(price, int(lp))
        if price < floor:
            price = int(floor)
        return {'action': 'counter', 'price': price}
    if turn not in (2, 3):
        return None
    if offer is None:
        return None
    if ask is None:
        ask = lp
    if offer >= 0.8 * ask:
        return None
    if offer >= floor and offer >= floor + 0.25 * margin:
        return {'action': 'accept'}
    k = 0.5 if turn == 2 else 0.4
    price = int(round(offer + (ask - offer) * k))
    if price >= ask:
        price = int(ask) - 1
    if price < floor:
        price = int(-(-floor // 1))
    if price < v['floor']:
        price = int(v['floor'])
    return {'action': 'counter', 'price': price}

