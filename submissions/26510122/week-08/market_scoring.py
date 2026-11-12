"""Inspectable Book/Saved contracts and Patch 3.6 margin-normalized Saved."""
import math

VERSION = 'round3-book-saved-v2'
SELLER_WEIGHTS = {'book':.5,'revenue':.3,'stars':.2}
BUYER_WEIGHTS = {'saved':.5,'forum':.3,'deals':.1,'posts':.1}
BUYER_RANKING_VERSION = 'round3-patch3.6'
BUYER_SCORE_SCALES = {'saved':'percentile','forum':'raw_rating_0_to_1','deals':'percentile','posts':'percentile'}
BUYER_ALL_CONTRACT = ('Buyer ALL = .5 Saved percentile + .3 raw forum rating (Wilson lower bound, 0..1) '
                      '+ .1 deals percentile + .1 posts percentile. Only forum uses its value directly. '
                      'Paid dislike power is unit*(N-1)^2 in total, paying only increases in that total. '
                      'The authoritative unit is inventory.dislike_power_unit; paid votes reduce shop cash and Book. '
                      'New negative-author hourly fees are separate; paid power alone is not the whole voting cost. '
                      'Counted votes require at least 50 settled deals and a different owner. '
                      'Voting eligibility and fees remain enforced by the market; a posted note is not a paid vote.')
SAVED_CONTRACT = ('Saved = sum of realized deal contributions / (deals+100), retroactive for all Round 3. '
                  'A usable deal contributes min(1, (list-paid)/(list-unit_cost)): share of the shop margin saved. '
                  'An unusable deal contributes -1, or 0 if refunded. Expensive goods get no extra weight. '
                  'The seller unit cost is private; category cost references are estimates, never verified costs. '
                  'Final Saved is the market value, not an average of quotes or estimated contributions. '
                  'Keep checkout validity and settlement likelihood; do not skip issued orders merely to chase a rate.')
BOOK_CONTRACT = ('Book = cash balance - starting capital - bankruptcy injections + unsold inventory acquisition cost. '
                 'Buying inventory at its paid cost is initially Book-neutral, not a profit or a score boost. '
                 'Sale spread, holding costs, food spoilage and warranty refunds change Book. '
                 'Stock credit persists at round end, but is not a cash buyback. '
                 'Shop ALL components use percentiles, not raw currency and stars. Buyer forum rating uses its raw 0..1 value.')

def saved_at_price(list_price, price, unit_cost=None):
    """Conditional usable contribution; unknown/nonpositive margin stays unknown."""
    if type(list_price) not in (int,float) or not math.isfinite(list_price) or list_price<=0:
        raise ValueError('positive finite list price required')
    if type(price) not in (int,float) or not math.isfinite(price) or price<0:
        raise ValueError('nonnegative finite price required')
    if (type(unit_cost) not in (int,float) or not math.isfinite(unit_cost)
            or not 0 <= unit_cost < list_price):
        return None
    return round(min(1., (list_price-price)/(list_price-unit_cost)), 6)


def buyer_cost_reference(product):
    """Public category averages are soft scenarios, not this seller's unit cost."""
    price = product.get('list_price')
    if type(price) is not int or price <= 0:
        return None
    category = str(product.get('category') or '')
    if product.get('kind') == 'food':
        share, source = .52, 'patch3.6 announced approximate food mean'
    elif category.startswith('가구/침구'):
        share, source = .61, 'patch3.6 announced approximate furniture/bedding mean'
    elif product.get('kind') == 'goods':
        share, source = .59, 'published goods grade odds and cost-range midpoints; prior, not observed mean'
    else:
        return None
    return {'unit_cost': price * share, 'cost_share': share, 'estimated': True, 'source': source,
            'note': 'Unknown seller unit, original grade and purchase-time surge can differ. '
                    'This is a negotiation scenario, not a certificate, floor price or guaranteed score.'}


def buyer_candidate(list_price, cap, *, cost_reference=None):
    cost = (cost_reference or {}).get('unit_cost')
    return {'metric':'saved','version':VERSION,
            'saved_if_usable_at_cap':saved_at_price(list_price,cap,cost),
            'cost_reference':cost_reference,
            'assumption':'cap is an authorization ceiling, NOT a seller quote or predicted price; '
                         'conditional margin saving only if a valid usable deal settles at that price. '
                         'With an estimated cost, the contribution is also estimated.',
            'virtual_zero_deals':100}

def book_metrics(inventory,now):
    stock=sum(max(0,int(u.get('cost') or 0)) for u in inventory.get('units',[])
              if u.get('grade')!='expired' and (u.get('expires_at') is None or u['expires_at']>now))
    balance=int(inventory.get('balance') or 0);start=int(inventory.get('start',5_000_000));injected=int(inventory.get('injected') or 0)
    cash=balance-start-injected
    return {'version':VERSION,'at':now,'cash_balance':balance,'starting_capital':start,
            'bankruptcy_injections':injected,'cash_earnings':cash,'stock_cost':stock,'book':cash+stock,
            'source':'inventory balance/start/injected and live unit acquisition costs'}

def terminal_credit(unit,ends_at):
    cost=max(0,int(unit.get('cost') or 0))
    if unit.get('grade')=='expired':return 0
    if unit.get('kind')=='food':
        expiry=unit.get('expires_at')
        return cost if type(expiry) in (int,float) and expiry>ends_at else 0
    return cost if unit.get('kind')=='goods' else 0

def continuation(product,cost,model_value,horizon_hours,hours_to_end):
    """Goods retain Book credit; no invented survival odds for newly drawn food."""
    value=max(0.,min(cost,float(model_value))) if type(model_value) in (int,float) and math.isfinite(model_value) else 0.
    future_hours=max(0.,hours_to_end-horizon_hours)
    floor=max(0.,cost-cost*.01*future_hours/24) if product.get('kind')=='goods' else 0.
    if horizon_hours>=hours_to_end:value=cost if product.get('kind')=='goods' else value
    return {'value':max(value,floor),'book_retention_floor':round(floor,4),
            'model_value':value,'future_holding_hours':future_hours,
            'contract':'Goods retain acquisition-cost Book credit less future carrying cost; '
                       'food continuation is an uncertain model hypothesis capped at cost, not guaranteed credit. '
                       'No resale or cash buyback occurs at the planning horizon.'}
