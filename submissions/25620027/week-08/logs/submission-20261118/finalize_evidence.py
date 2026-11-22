"""Reconcile preserved R3 observations, a bounded rating delta and public scores."""
import collections
import datetime
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
WORK = ROOT / '.work/report-submission-final-20261118'
SOURCES = OUT / 'sources'
KST = datetime.timezone(datetime.timedelta(hours=9))


def read(path):
    return json.loads(path.read_text())


def write(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def percentile(values, value):
    return (sum(x < value for x in values) + (sum(x == value for x in values) - 1) / 2) / (len(values) - 1)


def main():
    current = read(SOURCES / 'board-history-and-delivery.json')
    board = read(OUT / 'board-postclose-20261118T0959.json')
    history = current['history_selected']
    baseline = next(z for z in history if datetime.datetime.fromtimestamp(z['ts'], KST).strftime('%m/%d %H:%M') == '11/13 12:17')
    latest = max(history, key=lambda z: z['ts'])
    mine = lambda b: next(p for p in b['players'] if p.get('player') == 'SungJinho')
    changing_assets = {}
    for bname in ['shops', 'buyers']:
        old = next(r for r in board[bname] if r['name'] == '25620027')
        new = next(r for r in current['board'][bname] if r['name'] == '25620027')
        changing_assets[bname] = {k: [old.get(k), new.get(k)] for k in set(old) | set(new) if old.get(k) != new.get(k) and k not in ['card', 'alive', 'age', 'last_order']}
    baseline_evidence = {'action': 'stock catalogue forum posting expanded on 2026-11-13 12:43 KST',
        'action_evidence': 'preserved original REPORT operational entry and r3/forum.py',
        'baseline_kind': 'contemporaneously preserved pre-change observation, identified retrospectively; not a claimed pre-registered LAB worksheet',
        'baseline': baseline, 'last_preclose': latest, 'current_observed_at': current['observed_at'],
        'current_endpoint': current['endpoint'], 'current_headers': current['headers'],
        'postclose_player_identical': mine(board) == mine(current['board']),
        'score_before_and_after_close_equal': latest['digest']['board']['total'] == mine(board)['student_total'] == mine(current['board'])['student_total'],
        'postclose_asset_changes': changing_assets,
        'grading_snapshot': {'public_file_found': False, 'api_round3_is_historical_round_filter_not_timestamp': True,
            'warning': 'public live assets change after cutoff; neither public API observation is identified as the instructor archived grading file'},
        'forum_delivery': current['forum_delivery']}
    write('baseline-and-close-reconciliation.json', baseline_evidence)
    # Keep the explicit round endpoint observation separate from the earlier snapshot.
    write('board-round3-recheck-20261118.json', current['board'])

    old_source = SOURCES / 'rating-base-20261117T210958.json'
    old = read(old_source)
    delta = read(SOURCES / 'rating-delta-to-cutoff.json')
    assert not delta['late_backfill_sequences'], 'Unreconciled late backfill'
    groups = {}
    for buyer, shop, stars, n, first, last in old['rating_groups']:
        groups[(buyer, shop, stars)] = [n, first, last]
    for seq, ts, buyer, shop, stars in delta['result_rows']:
        row = groups.setdefault((buyer, shop, stars), [0, ts, ts])
        row[0] += 1
        row[1], row[2] = min(row[1], ts), max(row[2], ts)

    players = board['players']
    labels = [p['player'] for p in players]
    index = {name: i for i, name in enumerate(labels)}
    owner = {str((p.get('owner') or {}).get('login', '')).casefold(): p['player'] for p in players if p.get('owner')}
    maps = {'shop': {}, 'buyer': {}}
    for role, side in [('shop', 'shops'), ('buyer', 'buyers')]:
        for row in board[side]:
            label = owner.get(str((row.get('owner') or {}).get('login', '')).casefold())
            if label:
                maps[role][row['name']] = label
        for p in players:
            if p.get(role):
                maps[role][p[role]['name']] = p['player']
            maps[role].setdefault(p['player'], p['player'])
    urls = collections.defaultdict(list)
    for ts, url, name in old['joins'] + delta['joins']:
        urls[url.rstrip('/')].append((ts, maps['shop'].get(name)))
    for values in urls.values():
        values.sort()
    current_urls = {(r.get('card') or {}).get('url', '').rstrip('/'): maps['shop'].get(r['name']) for r in board['shops']}

    def resolve(shop, first, last):
        if shop in maps['shop']:
            return maps['shop'][shop]
        shop = shop.rstrip('/')
        history = urls.get(shop, [])
        owners = {v for t, v in history if v}
        if len(owners) == 1:
            return next(iter(owners))
        if len(owners) > 1:
            previous = [(t, v) for t, v in history if t <= first]
            label = previous[-1][1] if previous else None
            return label if label and not any(v != label for t, v in history if first < t <= last) else None
        return current_urls.get(shop)

    dist = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    excluded = 0
    for (buyer, raw_shop, stars), (n, first, last) in groups.items():
        b, s = maps['buyer'].get(buyer), resolve(raw_shop, first, last)
        if b not in index or s not in index:
            excluded += n
            continue
        row = dist[(b, s)][stars]
        row[0] += n
        row[1] = max(row[1], last)
    means = {key: sum(stars * n for stars, (n, last) in values.items()) / sum(n for n, last in values.values()) for key, values in dist.items()}
    def rating(shop, removed):
        values = [value for (buyer, target), value in means.items() if target == shop and buyer.casefold() not in removed]
        return (sum(values) + 15) / (len(values) + 5)
    students = [p for p in players if p.get('student') is True and not p.get('house')]
    selected = {p['player']: next(r for r in board['shops'] if r['name'] == p['shop']['name']) for p in students if p.get('shop')}
    selected_buyers = {p['player']: next(r for r in board['buyers'] if r['name'] == p['buyer']['name']) for p in students if p.get('buyer')}
    mismatches = [{'player': p, 'calculated': round(rating(p, set()), 2), 'official': row['stars_rank']} for p, row in selected.items() if round(rating(p, set()), 2) != row['stars_rank']]
    # A mismatch blocks promotion to an all-student counterfactual.
    assert not mismatches, mismatches
    removed = {'ovotnwjd-maker', 'dzzdd010'}
    newstars = {p: round(rating(p, removed), 2) for p in selected}
    bookvals = [r['book'] for r in selected.values()]
    revvals = [r['revenue'] for r in selected.values()]
    starvals = [r['stars_rank'] for r in selected.values()]
    savedvals = [r['saved'] for r in selected_buyers.values()]
    dealvals = [r['deals'] for r in selected_buyers.values()]
    postvals = [r['posts'] for r in selected_buyers.values()]
    for player in students:
        name = player['player']
        shop, buyer = selected[name], selected_buyers[name]
        rebuilt_shop = round(.5 * percentile(bookvals, shop['book']) + .3 * percentile(revvals, shop['revenue']) + .2 * percentile(starvals, shop['stars_rank']), 3)
        rebuilt_buyer = round(.5 * percentile(savedvals, buyer['saved']) + .3 * (buyer['forum'] or 0) + .1 * percentile(dealvals, buyer['deals']) + .1 * percentile(postvals, buyer['posts']), 3)
        assert rebuilt_shop == player['student_shop'], (name, 'shop score mismatch')
        assert rebuilt_buyer == player['student_buyer'], (name, 'buyer score mismatch')
        assert round(rebuilt_shop + rebuilt_buyer, 3) == player['student_total'], (name, 'total mismatch')
    rows = []
    for p in students:
        name = p['player']
        if name not in selected:
            continue
        r = selected[name]
        newshop = round(.5 * percentile(bookvals, r['book']) + .3 * percentile(revvals, r['revenue']) + .2 * percentile(list(newstars.values()), newstars[name]), 3)
        total = round(newshop + p['student_buyer'], 3)
        raters = {}
        for key in ['ovotnwjd-maker', 'DZZDD010']:
            matching = next((b for b in labels if b.casefold() == key.casefold()), None)
            dd = dist.get((matching, name))
            raters[key] = None if not dd else {'n': sum(n for n, last in dd.values()), 'mean': means[(matching, name)], 'histogram': {str(st): n for st, (n, last) in dd.items()}}
        rows.append({'player': name, 'official_total': p['student_total'], 'official_rank': 1 + sum(q['student_total'] > p['student_total'] for q in students), 'official_stars': r['stars_rank'], 'stars_without_both': newstars[name], 'total_without_both': total, 'delta': round(total - p['student_total'], 3), 'raters': raters})
    for row in rows:
        row['rank_without_both'] = 1 + sum(q['total_without_both'] > row['total_without_both'] for q in rows)
    rows.sort(key=lambda row: (row['official_rank'], row['player']))
    cells = [[index[b], index[s], round(means[(b, s)], 2), sum(n for n, last in dd.values()), [[st, n, last] for st, (n, last) in sorted(dd.items(), reverse=True)]] for (b, s), dd in dist.items()]
    matrix = {'p': labels, 's': [int(bool(p.get('student'))) for p in players], 'sr': [round(rating(p, set()), 3) for p in labels], 'nr': [sum(s == p for b, s in means) for p in labels], 'official_sr': [next((r.get('stars_rank') for r in board['shops'] if r['name'] == (p.get('shop') or {}).get('name')), None) for p in players], 'cells': cells, 'fixedT': delta['fixedT']}
    final = {'cutoff_kst': '2026-11-17T23:59:00+09:00', 'board_basis': '11/18 09:59 preserved official board; not the unpublished instructor cutoff archive',
        'rating_scope': 'verified numeric R3 reviews timestamped at or before cutoff',
        'method': 'preserved 21:09 aggregate plus bounded indexed delta; original remote full scan was interrupted at 75 seconds and is not counted as successful',
        'base_source_sha256': hashlib.sha256(old_source.read_bytes()).hexdigest(), 'delta_source_sha256': hashlib.sha256((SOURCES / 'rating-delta-to-cutoff.json').read_bytes()).hexdigest(),
        'scanned_delta_rows': delta['scanned'], 'delta_verified_reviews': len(delta['result_rows']), 'delta_read_seconds': delta['read_ended'] - delta['read_at'],
        'excluded_reviews': excluded, 'included_reviews': sum(c[3] for c in cells), 'pairs': len(cells),
        'official_rating_mismatches': mismatches, 'official_rating_matches': len(selected), 'official_shop_buyer_total_matches': len(students), 'students': len(students),
        'assumption': 'remove every rating by both buyer accounts from every shop uniformly, including five-star ratings; recompute all student shop scores together; freeze Book, revenue, buyer scores and roster at the preserved board; not an official correction or proof of operator identity',
        'rows': rows, 'heatmap': matrix}
    write('rating-cutoff-analysis.json', final)
    print(json.dumps({'rating_matches': len(selected), 'reviews': final['included_reviews'], 'pairs': len(cells), 'unmapped': excluded, 'top5': rows[:5], 'preclose_total': latest['digest']['board']['total'], 'baseline_total': baseline['digest']['board']['total']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
