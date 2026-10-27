"""Generate a truthful Round-2 dishonest-shop avoidance audit (offline)."""
from __future__ import annotations
from collections import deque
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

# The harness imports an OpenAI client but choose_shop is NOT called here.
# No API call is made; use a dummy key only if the environment lacks a real key.
os.environ.setdefault('OPENAI_API_KEY', 'offline-audit-no-api-calls')
import market_buyer as b

ROOT = Path(__file__).resolve().parent
SHOP_FILE = ROOT / '.market' / 'shops.json'
KIT_LOG = ROOT / 'logs' / 'market-kit.log'
OUT = ROOT / 'logs' / 'challenge-2' / 'round2-avoidance-audit.log'


def main() -> None:
    shops = b.load_shops(str(SHOP_FILE))
    summaries = [b.reputation_summary(s, b.load_memory()) for s in shops]
    flagged = next((s for s in summaries if s['name'] == 'maple-home'), None)
    if flagged is None:
        raise SystemExit('maple-home was not present in this market snapshot; no claim made.')
    safe = [s for s in summaries if s['low_stars'] == 0 and s['past_incidents'] == 0]
    eligible = sorted(safe if safe else summaries,
                      key=lambda s: (-s['risk_score'], s['name']))[:12]
    excluded = flagged['name'] not in {s['name'] for s in eligible}
    lines: deque[str] = deque(maxlen=10)
    if KIT_LOG.exists():
        with KIT_LOG.open('r', encoding='utf-8', errors='replace') as stream:
            for line in stream:
                if '[CHOOSE]' in line:
                    lines.append(re.sub(r"url='[^']*'", "url='[redacted]'", line.strip()))
    output = [
        'Week 07 - Round 2 dishonest-shop avoidance audit',
        'Recorded UTC: ' + datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'Market snapshot: .market/shops.json (latest locally downloaded by market kit)',
        f'Total shops: {len(shops)}',
        f'maple-home low-star reviews: {flagged["low_stars"]}',
        f'maple-home receipt-related incidents in own memory: {flagged["past_incidents"]}',
        f'maple-home risk score: {flagged["risk_score"]}',
        f'maple-home excluded from actual 12-shop selection pool: {excluded}',
        'Eligible shortlist: ' + ', '.join(s['name'] for s in eligible),
        'Last 10 real live buyer choices from local market-kit.log:',
        *(list(lines) if lines else ['(no CHOOSE lines found)']),
        'Result: ' + ('PASS (selection pool excludes suspect)' if excluded else 'NOT PROVEN'),
        'Note: low ratings plus repeated receipt mismatch reports are evidence of risk; ',
        'avoidance is shown by shortlist exclusion and observed choices; it does not ',
        'prove the market itself labeled the shop as dishonest.',
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text('\n'.join(output) + '\n', encoding='utf-8')
    print('\n'.join(output))
    if not excluded:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
