"""Read-only strategy status; no market or model API calls."""
import argparse
from datetime import datetime, timezone
import json
from r3.state import ROOT, Journal
from r3.adaptive import KEY, INTERVAL, MATURITY, MIN_SAMPLES


def status(journal):
    return {'evaluation_interval_seconds': INTERVAL, 'maturity_seconds': MATURITY,
            'minimum_mature_samples': MIN_SAMPLES, 'extra_model_calls': 0,
            'buyer': journal.get(KEY + 'buyer', {'version': 0, 'patch': {}, 'status': 'stable'}),
            'shop': journal.get(KEY + 'shop', {'version': 0, 'patch': {}, 'status': 'stable'}),
            'last_evaluated_at': journal.get(KEY + 'last_tick'),
            'recent_evaluations': journal.events('strategy_evaluation', 2),
            'changes': journal.events('strategy_change', 500)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--save', action='store_true', help='Save an immutable evidence export under logs/')
    args = parser.parse_args()
    report = status(Journal(ROOT / '.market'))
    if args.save:
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        path = ROOT / 'logs' / ('strategy-status-' + stamp + '.json')
        with path.open('x', encoding='utf-8') as out:
            json.dump(report, out, ensure_ascii=False, indent=2)
        report['export'] = str(path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
