"""Read-only pending claims. Exclude diagnostic purchases before any manual payout."""
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def main():
    diagnostics=set()
    for path in (ROOT/'logs/sales-experiment').glob('public-diagnostic-*.json'):
        diagnostics.update(row['task'] for row in json.loads(path.read_text(encoding='utf-8')).get('results',[]))
    requests=[json.loads(path.read_text(encoding='utf-8')) for path in (ROOT/'logs/refund-requests').glob('*.json')]
    genuine=[row for row in requests if row['task'] not in diagnostics]
    print(json.dumps({'diagnostic_claims_excluded':len(requests)-len(genuine),'genuine_requests':genuine,
                      'genuine_requested_amount':sum(row['amount'] for row in genuine),
                      'note':'Intake is not payment. Verify the original market purchase and claimant identity before external manual payment. Preserve separate payment evidence; never edit original request logs.'},ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
