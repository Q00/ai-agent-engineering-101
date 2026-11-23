"""Audit actual kit-v4 Round 3 logs; never invent settlements or ranking.

Run on the host with real `logs/market-kit.log`. Output stays local and can
be committed as independent evidence. Aggregate event types are deduplicated
by ledger sequence; A2A-completed and rejected payments do not count.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
PAID = re.compile(r"\[acp\] paid .* settled at ledger #(\d+)", re.I)
SOLD = re.compile(r"\[acp\] settled .* ledger #(\d+)", re.I)
FAILED = re.compile(r"\[acp\] (?:not paying|checkout refused|settle refused|payment failed):?\s*(.+)", re.I)
MARKET_VERSION = re.compile(r"\[kit\] market_kit (\d+)")
REGISTER = re.compile(r"\[market\] (shop|buyer) registered as (.*), round (\d+)")
BUY = re.compile(r"\[buyer\] \[shop selected\] name='([^']+)'")


def collect():
    files = sorted(LOGS.glob("market-kit*.log"))
    paid_ids, sold_ids = set(), set()
    refused, registrations = Counter(), []
    model_actions, reputation_avoids, price_quotes = 0, 0, 0
    all_version4 = False
    samples = []
    for file in files:
        with file.open(encoding="utf-8", errors="replace") as fh:
            for line_no, line in enumerate(fh, 1):
                vm = MARKET_VERSION.search(line)
                if vm and vm.group(1) == "4":
                    all_version4 = True
                reg = REGISTER.search(line)
                if reg:
                    registrations.append({"role": reg.group(1), "name": reg.group(2),
                                          "round": int(reg.group(3))})
                m = PAID.search(line)
                if m:
                    paid_ids.add(m.group(1))
                    if len(samples) < 12:
                        samples.append({"kind": "buyer-paid", "file": file.name, "line": line_no,
                                        "ledger": m.group(1)})
                m = SOLD.search(line)
                if m:
                    sold_ids.add(m.group(1))
                    if len(samples) < 12:
                        samples.append({"kind": "shop-sold", "file": file.name, "line": line_no,
                                        "ledger": m.group(1)})
                m = FAILED.search(line)
                if m:
                    refused[m.group(1).strip()[:120]] += 1
                if "[harness] model=" in line:
                    model_actions += 1
                if "[safety selection] excluded=" in line:
                    reputation_avoids += 1
                if "[pricing policy]" in line:
                    price_quotes += 1
    if not all_version4:
        raise RuntimeError("No kit v4 version marker; do not label prior-round logs as Round 3")
    result = {
        "source": [x.name for x in files],
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "shop_round3_settlement_events_distinct_ledger": len(sold_ids),
        "buyer_round3_settlement_events_distinct_ledger": len(paid_ids),
        "shop_at_least_20": len(sold_ids) >= 20,
        "buyer_at_least_20": len(paid_ids) >= 20,
        "observed_registrations": registrations[-8:],
        "acp_refusal_reasons_counts": dict(refused.most_common(20)),
        "model_actions_logged": model_actions,
        "reputation_exclusion_logs": reputation_avoids,
        "seller_price_quotes_logged": price_quotes,
        "sample_source_line_numbers": samples,
        "not_market_final_rank": True,
        "caution": "Log event counts should be cross-checked with the Round 3 official dashboard. Historical/rotated log contamination should be excluded.",
    }
    target = LOGS / "round3-summary.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    result = collect()
    print(json.dumps(result, ensure_ascii=False, indent=2))
