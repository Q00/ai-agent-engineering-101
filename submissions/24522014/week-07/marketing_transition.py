"""Preserve read-only market snapshots around the authorized local restart."""
from datetime import datetime, timezone, timedelta
import argparse
import json
from pathlib import Path
import ssl
import sys
import httpx

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['before', 'live'])
    parser.add_argument('--profile', choices=['marketing', 'purchase'], default='marketing')
    args = parser.parse_args()
    phase = args.phase
    directory = 'marketing' if args.profile == 'marketing' else 'purchase-experience'
    (ROOT / 'logs' / directory).mkdir(exist_ok=True)
    with httpx.Client(verify=ssl.create_default_context(), timeout=45) as client:
        response = client.get('https://market.ourolabs.ai/api/board?round=2')
        response.raise_for_status()
        board = response.json()
        response = client.get('https://market.ourolabs.ai/api/ledger?after=-1')
        response.raise_for_status()
        head = response.json()[0]['seq']
    shop = next(row for row in board['shops'] if row['name'] == 'kcwarni')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = ROOT / 'logs' / directory / f'transition-{phase}-{stamp}.json'
    snapshot = {'checked_at': datetime.now(timezone.utc).isoformat(), 'shop': shop, 'head_seq': head}
    state_path = ROOT / '.market' / f'{args.profile}-deployment.json'
    if phase == 'before':
        state = {'cutoff_seq': head, 'previous_url': shop['card']['url'], 'before_snapshot': str(output.relative_to(ROOT))}
    else:
        state = json.loads(state_path.read_text(encoding='utf-8'))
        process = json.loads((ROOT / '.market/process.json').read_text(encoding='utf-8-sig'))
        started = datetime.strptime(process['started'], '%Y%m%d-%H%M%S').replace(tzinfo=timezone(timedelta(hours=9)))
        state.update(started_at=started.isoformat(), url=shop['card']['url'], process=process, registered_at=snapshot['checked_at'])
        assert shop['card']['version'] == process['card_version']
        previous = ROOT / f"logs/{directory}/memory-before-{process['started']}.jsonl"
        assert (ROOT / '.market/buyer-memory.jsonl').read_bytes().startswith(previous.read_bytes())
        snapshot['memory_preserved'] = True
        promise_path = ROOT / '.market/review-promise-deployment.json'
        promise = json.loads(promise_path.read_text(encoding='utf-8'))
        promise.update(url=state['url'], process=process, registered_at=snapshot['checked_at'])
        promise_path.write_text(json.dumps(promise, ensure_ascii=False, indent=2), encoding='utf-8')
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    snapshot['deployment'] = state
    output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'phase': phase, 'url': shop['card']['url'], 'version': shop['card']['version'], 'snapshot': str(output)}, indent=2))


if __name__ == '__main__':
    main()
