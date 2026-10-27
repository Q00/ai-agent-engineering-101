"""Save an OBSERVED public-market review snapshot for adaptive seller pricing.

Example using values reported by the user from a Round-2 dashboard:
    python seed_own_reviews.py --reviews 83 --avg-stars 4.48
Update these values when a NEW dashboard snapshot is observed.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reviews', type=int, required=True)
    parser.add_argument('--avg-stars', type=float, required=True)
    args = parser.parse_args()
    if args.reviews < 0 or not (1 <= args.avg_stars <= 5):
        parser.error('Review count and average stars are out of range')
    path = Path(__file__).resolve().parent / '.market' / 'own-review-summary.json'
    path.parent.mkdir(exist_ok=True)
    record = {
        'shop': 'chanarun', 'review_count': args.reviews,
        'avg_stars': args.avg_stars,
        'snapshot_recorded_utc': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'source': 'User-observed Agent Market dashboard (not a live API feed)',
    }
    path.write_text(json.dumps(record, indent=2) + '\n', encoding='utf-8')
    print(f'Saved review snapshot to {path.name}: {args.reviews} reviews, {args.avg_stars} stars')
