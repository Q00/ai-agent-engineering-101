"""Score strategy office: attainable thresholds and observed quality downside.

All gains and future throughput are scenarios; no unvalidated positive rating
prediction or additional payment authority is introduced.
"""
from collections import defaultdict
from datetime import datetime
import math
import statistics
import time
from .state import identity

KEY = 'score-campaign:v32'


def enabled(config):
    return config.get('score_campaign_enabled') and config.get('finalday_departments_enabled')


def context(journal, now=None):
    now = time.time() if now is None else now
    row = journal.get(KEY, {})
    if (not 0 <= now-row.get('at', 0) < 180 or now >= row.get('expires_at', 0)
            or row.get('rules_hash') != journal.get('market_rules', {}).get('sha256')
            or not 0 <= now-journal.get('board_checked_at',0) < 180
            or row.get('id') != identity({k:v for k,v in row.items() if k!='id'})):
        return {}
    return row


def saved_goals(board, own, rate, hours, velocities=None):
    from .meta import eligible_rows, rank_gain
    if (not own or rate <= 0 or hours <= 0
            or type(own.get('saved')) not in (int,float) or not math.isfinite(own['saved'])
            or not 0<=own['saved']<=1 or type(own.get('deals')) not in (int,float)):
        return [], []
    peers = eligible_rows(board, 'buyer', 'seochanit')
    above = sorted({r['saved'] for r in peers if type(r.get('saved')) in (int,float)
                    and r['saved'] > own['saved']})
    goals = []
    covered = set()
    bids = {min(.999, own['saved']+.002)}
    for value in above:
        target = min(1, value+.00001)
        gain = rank_gain(dict(board,buyer_forum_metric='raw'), 'buyer', 'seochanit',
                         {'saved':target-own['saved']})
        tags = {threshold for threshold in (0, .01, .02)
                if threshold not in covered and gain is not None and gain >= threshold}
        if not tags:
            continue
        covered.update(tags)
        means = []
        drift = max([0]+[(velocities or {}).get(r.get('name'),0) for r in peers if r.get('saved')==value])
        for duration in sorted({min(hours,4),min(hours,8)}):
            future = rate*duration
            if future <= 0:
                continue
            projected = min(1,target+drift*duration)
            required = (projected*(own['deals']+100+future)-own['saved']*(own['deals']+100))/future
            if 0 < required < 1:
                means.append({'hours':duration,'future_deals':future,'required_mean':required,
                              'target_scenario':projected})
                bids.add(required)
        goals.append({'target':target,'all_gain_scenario':gain,'completion_scenarios':means})
        if .02 in covered:
            break
    return goals, sorted(bids)


def quality_cells(sales, reviews, catalog, current_stars):
    cells = defaultdict(lambda:defaultdict(list))
    seen = set()
    for r in reviews:
        ledger = str(r.get('ledger'))
        author = r.get('author')
        sale = sales.get(ledger)
        star = r.get('stars')
        if (not sale or not author or not r.get('identity_known') or ledger in seen
                or r.get('source') != 'verified_market_result_and_accepted_own_task'
                or type(star) not in (int,float) or not 1 <= star <= 5):
            continue
        seen.add(ledger)
        kind = catalog.get(sale.get('sku'),{}).get('kind')
        cells[str(kind)+':'+str(sale.get('grade'))][author].append(star)
    result = {}
    for key, accounts in cells.items():
        means = [statistics.mean(v) for v in accounts.values()]
        n = sum(map(len,accounts.values()))
        mean = statistics.mean(means)
        upper = mean+1.96*statistics.stdev(means)/math.sqrt(len(means)) if len(means)>1 else 5
        result[key] = {'reviews':n,'accounts':len(means),'account_mean':mean,
                       'upper95_scenario':upper,
                       'supported_downside':n>=20 and len(means)>=5 and upper<current_stars}
    return result


def update(journal, config, now=None):
    if not enabled(config):
        return None
    now = time.time() if now is None else now
    if not 0 <= now-journal.get('board_checked_at',0) < 180:
        return None
    board = journal.snapshot('board.json',{})
    own = next((r for r in board.get('buyers',[]) if r.get('name')=='seochanit'),{})
    shop = next((r for r in board.get('shops',[]) if r.get('name')=='seochanit'),{})
    end = datetime.fromisoformat(config['market_end_at']).timestamp() if config.get('market_end_at') else now
    hours = max(0,(end-now)/3600)
    rate = sum(0 <= now-r.get('paid_at',r['_ts']) < 3600 for r in journal.events('purchase',2000))
    rules = journal.get('market_rules',{}).get('sha256')
    anchor = journal.get('score-campaign-anchor:v32',{})
    velocities = journal.get(KEY,{}).get('peer_velocity_per_hour',{})
    values = {r['name']:r['saved'] for r in board.get('buyers',[]) if type(r.get('saved')) in (int,float)}
    if anchor.get('rules_hash')!=rules or not 0<=now-anchor.get('at',0)<3600:
        velocities = {}
        journal.put('score-campaign-anchor:v32',dict(at=now,rules_hash=rules,values=values))
    elif now-anchor['at']>=900:
        duration=(now-anchor['at'])/3600
        velocities={name:(v-anchor['values'][name])/duration for name,v in values.items() if name in anchor['values']}
        journal.put('score-campaign-anchor:v32',dict(at=now,rules_hash=rules,values=values))
    goals, bids = saved_goals(board,own,rate,hours,velocities)
    sales = {r['_event_id'].partition('settlement:')[2]:r for r in journal.events('shop_settled',20000)}
    reviews = [r for r in journal.events('shop_review_observed',20000) if 0<=now-r['_ts']<172800]
    cells = quality_cells(sales,reviews,journal.snapshot('catalog.json',{}),shop.get('stars_rank') or 0)
    row = {'department':'점수전략실','at':now,'expires_at':now+180,
           'rules_hash':journal.get('market_rules',{}).get('sha256'),
           'remaining_hours':hours,'paid_per_hour':rate,'saved_goals':goals,
           'peer_velocity_per_hour':velocities,
           'bid_targets':bids,'quality_cells':cells,
           'limits':['Static public percentile proxy, not official guaranteed ALL.',
                     'Required purchase means and quality bounds are observational scenarios.']}
    row['id'] = identity(row)
    journal.put(KEY,row)
    journal.observe('score_campaign_prepared', {k:v for k,v in row.items() if k!='quality_cells'})
    return row


def safe_service(unit, catalog, spec, packet, now=None):
    """Keep poorly rated C-food clearance explicit; do not infer buyer consent."""
    from .economy import grade
    g = grade(unit,catalog,now)
    cell = packet.get('quality_cells',{}).get(str(unit.get('kind'))+':'+str(g),{})
    if unit.get('kind')!='food' or g!='C' or not cell.get('supported_downside'):
        return True
    return bool(spec.get('explicit_use') and spec.get('explicit_grade')
                and 'C' in (spec.get('grades') or []))


def public_demand_categories(journal, now):
    """Public requests are content relevance hints, never trading instructions."""
    desired = set()
    for row in journal.events('forum_observation',1000):
        if (row.get('wanted') and row.get('role')=='buyer' and row.get('author')!='seochanit'
                and 0<=now-row['_ts']<900):
            desired.update(row.get('categories') or [])
    return desired
