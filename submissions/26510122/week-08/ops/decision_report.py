"""Read-only decision -> execution -> outcome report from local raw evidence."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from decision_audit import journeys
from market_memory import events


def report(runtime, role, *, episode=None, limit=10):
    name='shop-events.jsonl' if role=='seller' else 'buyer-events.jsonl'
    rows=journeys(events(Path(runtime)/'logs'/name))
    if episode is not None:rows=[row for row in rows if row['episode_id']==episode]
    return {'role':role,'episodes_total':len(rows),'episodes':rows[-limit:],
            'note':'Read-only observed episodes. Pending/expired are unknown payment outcomes. '
                   'Paid is payment-time evidence, not mature warranty-adjusted Book/Saved or causal policy credit.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--role',choices=('seller','buyer'),required=True)
    parser.add_argument('--episode')
    parser.add_argument('--limit',type=int,default=10)
    args=parser.parse_args()
    if args.limit<1:parser.error('--limit must be positive')
    print(json.dumps(report(args.runtime,args.role,episode=args.episode,limit=args.limit),ensure_ascii=False,indent=2))
