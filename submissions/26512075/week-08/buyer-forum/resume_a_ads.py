"""Quarantine one uncertain delivery without reposting, then resume saved A ads."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import time

import publisher
from advertise_bc_once import emit, run


def hold_unknown(state, delivery_id, now=None):
    now = time.time() if now is None else now
    with publisher.lock(state):
        history = publisher.read_jsonl(state / publisher.JOURNAL)
        matches = [r for r in publisher.forum_delivery.resolved(state, history)
                   if r.get('id') == delivery_id]
        if len(matches) != 1:
            raise ValueError('expected one existing delivery')
        entry = matches[0]
        if (entry.get('as'), entry.get('shop'), entry.get('market')) != (
                'shop', 'helenjeon101', 'https://market.ourolabs.ai'):
            raise ValueError('unexpected delivery identity')
        if entry.get('state') not in ('unknown', 'held_unknown'):
            raise ValueError('only completed uncertain attempts may be held')
        if now < max(entry['at'] + 300, entry.get('next_at', 0)):
            raise ValueError('delivery cooldown has not elapsed')
        queue = publisher.read_jsonl(state / publisher.QUEUE)
        # Remove first: interruption leaves the original unknown blocker intact.
        publisher.write_jsonl(state / publisher.QUEUE,
                             [r for r in queue if r.get('id') != delivery_id])
        if entry['state'] == 'unknown':
            held = {**entry, 'state': 'held_unknown', 'held_at': now,
                    'reason': 'User requested A campaign resume; do not retry this uncertain delivery',
                    'next_at': max(entry['at'] + 300, entry.get('next_at', 0))}
            with (state / publisher.JOURNAL).open('a') as stream:
                stream.write(json.dumps(held, ensure_ascii=False) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
        emit('held_unknown_no_repost', id=delivery_id, sku=entry.get('sku'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--hold-delivery', required=True)
    args = parser.parse_args()
    targets = json.loads(args.manifest.read_text())['targets']
    if not targets or any(t.get('grade') != 'A' for t in targets):
        raise ValueError('expected saved A-grade targets')
    with (args.state / 'advertise-a-after-bc.lock').open('a') as guard:
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        hold_unknown(args.state, args.hold_delivery)
        run(args.state, targets=targets, grades=('A',), markup_percent=200, require_json=True)


if __name__ == '__main__':
    main()
