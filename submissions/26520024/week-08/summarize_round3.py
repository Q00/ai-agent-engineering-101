"""Reproduce the final report from read-only K/J memories and public board data.

No agent/model invocation, market registration, transaction, or credential read.
Outputs contain aggregates and whitelisted audit fields, never raw DB payloads.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3

KINDS = ('order_seen', 'negotiation', 'payment', 'settlement', 'seller_settlement',
         'seller_failure', 'policy_fallback', 'score_snapshot', 'seller_checkout',
         'restock_intent', 'stock_unit', 'forum_queued')
CUTOFF = datetime(2026, 11, 17, 14, 59, tzinfo=timezone.utc).timestamp()
ROOT = Path(__file__).resolve().parent


def iso(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc).isoformat()


def read_events(path, label):
    path = Path(path).resolve()
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows = db.execute('SELECT kind,event_key,ts,data FROM events WHERE kind IN (' +
                          ','.join('?' for _ in KINDS) + ') ORDER BY id', KINDS)
        for kind, key, stamp, raw in rows:
            yield dict(kind=kind, key=str(key), ts=stamp, data=json.loads(raw), source=label)


def merge_events(sources):
    merged, duplicates, changed = {}, Counter(), Counter()
    for label, rows in sources:
        for event in rows:
            identity = event['kind'], event['key']
            previous = merged.get(identity)
            if previous is None:
                merged[identity] = event
                continue
            duplicates[event['kind']] += 1
            if previous['data'] != event['data']:
                changed[event['kind']] += 1
            # K's original event takes priority over J's reconstructed projection.
            old_partial = bool(previous['data'].get('recovery_scope') or previous['data'].get('recovered_from'))
            new_partial = bool(event['data'].get('recovery_scope') or event['data'].get('recovered_from'))
            if old_partial and not new_partial:
                merged[identity] = event
    return list(merged.values()), dict(duplicates), dict(changed)


def reason_category(text):
    text = str(text or '')
    low = text.lower()
    if 'no matching unreserved inventory' in low:
        return 'no_matching_unreserved_inventory'
    if 'stale or from the future' in low or 'inventory unavailable' in low:
        return 'inventory_freshness_or_clock'
    if 'price violates' in low:
        return 'local_price_floor_guard'
    if 'ask cannot rise' in low or 'honor the quoted' in low:
        return 'local_quote_consistency_guard'
    if text == 'walk':
        return 'own_policy_walk'
    if text == 'turn_limit':
        return 'negotiation_turn_limit'
    if 'receipt' in low and ('differs' in low or 'invalid' in low or 'mismatch' in low or 'receipterror' in low):
        return 'receipt_validation'
    if 'agentcard changed the shop origin' in low:
        return 'agent_card_origin_guard'
    if 'database is locked' in low:
        return 'remote_database_locked'
    status = re.search(r'\b(4\d\d|5\d\d)\b', text)
    if status and ('http' in low or 'error' in low):
        return 'remote_http_' + status.group(1)
    if 'timeout' in low or 'timed out' in low:
        return 'timeout'
    if any(x in low for x in ('disconnected', 'remoteprotocolerror', 'readerror', 'connecterror')):
        return 'network_connection'
    if 'deadline' in low or 'expired' in low:
        return 'deadline_or_expiry'
    if 'in stock' in low or 'no stock' in low:
        return 'checkout_inventory'
    if not text:
        return 'unspecified'
    code = re.match(r'^([A-Za-z_][A-Za-z0-9_]{0,55})(?::|$)', text)
    return code.group(1) if code else 'other_recorded_reason'


def read_own_public_settlements(path, label):
    with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows=db.execute("SELECT event_key,ts,data FROM events WHERE kind='market_settle' "
                        "AND json_extract(data,'$.buyer')='leeuichann'")
        for key,stamp,raw in rows:
            data=json.loads(raw)
            if data.get('source')=='public_ledger':
                yield {'seq':data.get('seq',int(key)),'ts':stamp,'source':label,
                       'data':{k:data[k] for k in ('order','buyer','item','amount','price') if k in data}}


def summarize(events, cutoff=CUTOFF, public_settlements=()):
    before = [e for e in events if e['ts'] < cutoff]
    groups = defaultdict(list)
    for e in before:
        groups[e['kind']].append(e)
    payments = {e['key']: e['data'] for e in groups['payment']}
    public={p['seq']:p for p in public_settlements if p['ts']<cutoff}
    public_orders={str(p['data'].get('order')) for p in public.values()}
    outcomes, buyer_causes, seller_causes, fallbacks, audit = Counter(), Counter(), Counter(), Counter(), []
    for e in groups['negotiation']:
        data, pay = e['data'], payments.get(e['key'], {})
        outcome = data.get('outcome', 'unknown')
        if pay.get('paid') is True:
            category = 'paid'
        elif e['key'] in public_orders:
            category = 'server_settled_unconfirmed_locally'
        elif pay.get('paid') is False:
            category = 'payment_rejected'
        elif outcome == 'receipt_pending':
            category = 'receipt_without_payment_evidence'
        elif outcome == 'no_deal':
            category = reason_category(data.get('reason')) if data.get('reason') else 'no_deal_without_detailed_reason'
        else:
            category = reason_category(data.get('error', data.get('reason', outcome)))
        outcomes[category] += 1
        if category not in ('paid','server_settled_unconfirmed_locally'):
            buyer_causes[category] += 1
        audit.append(dict(source=e['source'], kind=e['kind'], key=e['key'], at_utc=iso(e['ts']), category=category))
    for e in groups['seller_failure']:
        category = reason_category(e['data'].get('reason'))
        seller_causes[category] += 1
        audit.append(dict(source=e['source'], kind=e['kind'], key=e['key'], at_utc=iso(e['ts']), category=category))
    for e in groups['policy_fallback']:
        fallbacks[str(e['data'].get('reason', 'unknown'))] += 1
    paid = [e for e in groups['payment'] if e['data'].get('paid') is True]
    failed_pay = [e for e in groups['payment'] if e['data'].get('paid') is False]
    payment_failures = Counter(reason_category(e['data'].get('reason')) for e in failed_pay)
    local_ledgers={e['data']['ledger'] for e in paid if type(e['data'].get('ledger')) is int}
    disagreements=[{'seq':seq,'at_utc':iso(p['ts']),'order':p['data'].get('order'),'source':p['source']}
                   for seq,p in public.items() if seq not in local_ledgers]
    scope = {kind: {'count': len(rows), 'first_utc': iso(min(e['ts'] for e in rows)),
                    'last_utc': iso(max(e['ts'] for e in rows)),
                    'sources': dict(Counter(e['source'] for e in rows))}
             for kind, rows in groups.items() if rows}
    result = {'cutoff_exclusive_utc': iso(cutoff), 'scope_by_kind': scope,
              'local_paid_purchases': len(paid), 'local_payment_rejections': len(failed_pay),
              'public_ledger_purchases_before_close':len(public),
              'confirmed_purchase_ledgers_union':len(local_ledgers|set(public)),
              'public_settlements_without_local_paid_record':disagreements,
              'local_seller_settlement_callbacks': len(groups['seller_settlement']),
              'buyer_outcomes_by_unique_order': dict(outcomes),
              'buyer_nonpaid_reasons_by_unique_order': dict(buyer_causes),
              'seller_refusals_by_unique_task': dict(seller_causes),
              'payment_rejection_reasons': dict(payment_failures),
              'model_fallback_diagnostics_not_refusals': dict(fallbacks),
              'checkout_events': dict(Counter(e['data'].get('action', 'unknown') for e in groups['seller_checkout'])),
              'queued_posts_by_role': dict(Counter(e['data'].get('as', 'unspecified') for e in groups['forum_queued']))}
    return result, audit


def class_result(players, login='LeeUichann'):
    rows = sorted([p for p in players if p.get('student')], key=lambda p: p['student_total'], reverse=True)
    index = next(i for i, p in enumerate(rows) if p['player'].lower() == login.lower())
    own = rows[index]
    return dict(rank=index+1, cohort_size=len(rows), percentile=own['student_pct'],
                class_shop=own['student_shop'], class_buyer=own['student_buyer'],
                class_total=own['student_total'], everyone_total=own['total'])


def baseline(path, events):
    data = json.loads(Path(path).read_text())
    prediction = data['score_evaluation']['next_prediction']
    if prediction['method'] != 'cold_start_persistence':
        raise ValueError('A rate projection is not an observed baseline')
    own = prediction['projected_own']
    stamp = prediction['made_at']
    candidates = [e for e in events if e['kind'] == 'score_snapshot' and
                  abs(e['ts']-stamp) <= 900 and e['data'].get('own') == own]
    match = min(candidates, key=lambda e: abs(e['ts']-stamp)) if candidates else None
    try:
        source_name = str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        source_name = Path(path).name
    return {'at_utc': iso(match['ts'] if match else stamp), 'own': own,
            'source': 'original_K_score_snapshot' if match else 'saved_cold_start_persistence_values',
            'original_event_key': match['key'] if match else None,
            'watch_log': source_name,
            'rule': 'Patch 3.5, list-price-based Saved; not numerically comparable to final 3.6 Saved',
            'class_rank_recorded': False}


def score_checkpoints(events):
    rows = [e for e in events if e['kind'] == 'score_snapshot']
    before, after = [e for e in rows if e['ts'] < CUTOFF], [e for e in rows if e['ts'] >= CUTOFF]
    choices = {'last_before_close': max(before, key=lambda e:e['ts']) if before else None,
               'first_after_close': min(after, key=lambda e:e['ts']) if after else None,
               'latest_local_observation': max(rows, key=lambda e:e['ts']) if rows else None}
    return {name: {'at_utc':iso(e['ts']), 'source':e['source'], 'event_key':e['key'], 'own':e['data']['own']}
            for name,e in choices.items() if e}


def fetch_public_board():
    import httpx
    url = 'https://market.ourolabs.ai/api/board?round=3'
    with httpx.Client(transport=httpx.HTTPTransport(retries=0), timeout=20) as client:
        response = client.get(url, headers={'Cache-Control':'no-cache'})
        response.raise_for_status()
        raw = response.json()
    allowed = ('name','alive','deals','orders','revenue','profit','book','stock_units','stock_cost',
               'all','saved','surplus','forum','posts','stars','stars_rank','reviews','visits',
               'refunds','broken','served','bankruptcies')
    own = {}
    for plural, singular in (('shops','shop'),('buyers','buyer')):
        row = next(p for p in raw[plural] if p['name']=='leeuichann')
        own[singular] = {k:row[k] for k in allowed if k in row}
    player_fields = ('player','house','student','student_shop','student_buyer','student_total','student_pct','total')
    players = [{k:p[k] for k in player_fields if k in p} for p in raw['players']]
    host_time=datetime.now(timezone.utc).isoformat()
    server_time=parsedate_to_datetime(response.headers['date']).isoformat() if response.headers.get('date') else None
    return {'source_url':url, 'retrieved_at_utc':server_time or host_time,
            'retrieved_at_host_utc':host_time,'retrieval_time_basis':'HTTPS server Date' if server_time else 'host clock',
            'server_date':response.headers.get('date'),
            'raw_response_sha256':hashlib.sha256(response.content).hexdigest(),
            'scope':'Public own metrics and player ranking fields; cards/keys/avatars omitted',
            'round':raw['round'], 'own':own, 'players':players,
            'warning':'Submission-time query; not an independently obtained 2026-11-17 23:59 KST frozen board'}


def retained_log_evidence():
    files = ('logs/market-kit.log.1','logs/market-kit.log','logs/j-resume-20261115.log')
    results, examples, scopes = {}, defaultdict(list), []
    for name in files:
        path = ROOT/name
        if not path.is_file():
            continue
        first, last = None, None
        with path.open(encoding='utf-8',errors='replace') as stream:
            for number,line in enumerate(stream,1):
                category = None
                match = re.match(r'^\d\d:\d\d:\d\d \[market\] ledger #(\d+):.*verified (True|False)',line)
                if match:
                    results[match[1]] = match[2]=='True'
                    category = 'verified_result' if match[2]=='True' else 'unverified_result'
                start = line.find('{')
                if start>=0:
                    try:
                        row=json.loads(line[start:])
                    except ValueError:
                        row={}
                    if isinstance(row,dict) and row.get('event') and row.get('time'):
                        first=first or row['time'];last=row['time']
                    if isinstance(row,dict) and row.get('event')=='r3_seller_refused':
                        category=reason_category(row.get('reason'))
                if category and (len(examples[category])<2 or name=='logs/market-kit.log'):
                    example={'file':name,'line':number,'text':line.strip()[:850],
                             'truncated':len(line.strip())>850}
                    if len(examples[category])>=4:examples[category].pop()
                    examples[category].append(example)
        scopes.append({'file':name,'bytes':path.stat().st_size,
                       'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                       'first_structured_host_utc':first,'last_structured_host_utc':last})
    return {'scope':'Retained, rotated raw-log window plus the Nov 15 excerpt; not the complete round',
            'timestamps':'Structured log time is host UTC; HH:MM:SS prefix is host local time, not corrected market time',
            'unique_result_lines':len(results),'verified_result_lines':sum(results.values()),
            'files':scopes,'quotes':dict(examples)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--k-db',required=True,type=Path)
    parser.add_argument('--j-db',required=True,type=Path)
    parser.add_argument('--k-manifest',type=Path)
    parser.add_argument('--output-dir',required=True,type=Path)
    args=parser.parse_args()
    out=args.output_dir.resolve()
    out.mkdir(parents=True,exist_ok=False)
    events,duplicates,changed=merge_events([('K',read_events(args.k_db,'K')),('J',read_events(args.j_db,'J'))])
    public={}
    for label,path in [('K',args.k_db),('J',args.j_db)]:
        for row in read_own_public_settlements(path,label):public[row['seq']]=row
    summary,audit=summarize(events,public_settlements=public.values())
    board=fetch_public_board()
    base=baseline(ROOT/'logs/trend-watch/20261110T040910274456Z.json',events)
    early,_=summarize(events,datetime.fromisoformat(base['at_utc']).timestamp(),public.values())
    proofs=[]
    if summary['public_settlements_without_local_paid_record']:
        import httpx
        with httpx.Client(transport=httpx.HTTPTransport(retries=0),timeout=20) as client:
            for item in summary['public_settlements_without_local_paid_record']:
                seq=item['seq']
                response=client.get('https://market.ourolabs.ai/api/ledger',params={'after':seq-1})
                response.raise_for_status()
                actual=next((r for r in response.json() if r.get('seq')==seq),None)
                if actual is None or actual.get('kind')!='settle' or actual.get('data',{}).get('order')!=item['order']:
                    raise RuntimeError('Public/local disagreement could not be verified against the source ledger')
                proofs.append({'source_url':str(response.request.url),'server_date':response.headers.get('date'),
                               'seq':seq,'ts':actual['ts'],'kind':actual['kind'],
                               'data':{k:actual['data'][k] for k in ('order','buyer','item','amount','price') if k in actual['data']}})
    result={'generated_at_host_utc':datetime.now(timezone.utc).isoformat(),
            'server_snapshot_utc':board['retrieved_at_utc'],
            'method':'Original K and J events deduplicated by (kind,event_key); original records preferred over recovery projections',
            'duplicate_keys':duplicates,'duplicate_keys_with_different_payloads':changed,
            'baseline':base,'baseline_cumulative_local_counts':early,
            'observed_submission_board':{'retrieved_at_utc':board['retrieved_at_utc'],'own':board['own'],
                                          'class_players':class_result(board['players'])},
            'local_score_checkpoints':score_checkpoints(events),
            'round_events':summary,'retained_raw_log_evidence':retained_log_evidence(),
            'verified_public_payment_disagreements':proofs,
            'limitations':['Class baseline rank was not recorded; no reconstructed rank is presented as official',
                           'Saved changed from list-price share to margin share on Nov 12; its before/after delta is not a policy effect',
                           'Current Book/Saved can differ from close-time observations; the instructor frozen board determines the grade',
                           'Seller failures count unique task IDs, not all network attempts; fallback is not itself a failed transaction',
                           'One positive server settlement has no matching local paid acknowledgment; the exact public ledger is saved separately']}
    if args.k_manifest:
        manifest=json.loads(args.k_manifest.read_text())
        result['k_export_provenance']={k:manifest[k] for k in ('host','created_utc','sha256','integrity','event_counts')}
    for name,data in [('board.json',board),('results.json',result)]:
        (out/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    with (out/'refusal-audit.jsonl').open('w') as stream:
        for row in sorted(audit,key=lambda r:(r['at_utc'],r['kind'],r['key'])):
            stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    print(json.dumps({'output_dir':str(out),'class_players':result['observed_submission_board']['class_players'],
                      'local_paid_purchases':summary['local_paid_purchases'],
                      'confirmed_purchase_ledgers_union':summary['confirmed_purchase_ledgers_union'],
                      'seller_callbacks':summary['local_seller_settlement_callbacks'],
                      'buyer_outcomes':summary['buyer_outcomes_by_unique_order'],
                      'seller_refusals':summary['seller_refusals_by_unique_task'],
                      'baseline_source':base['source']},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
