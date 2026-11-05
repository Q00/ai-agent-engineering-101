"""Read-only: when a read figure differs from the settled price, was it a misreading or did the deal close lower after our own offer? Per deal, not per reply. Prices compared as ratios only."""
import glob
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import buyer_fast as fast

BOTH = {"group_a": True, "group_b": True}
paid = {}
for line in (ROOT / ".market/payments.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
    try:
        row = json.loads(line)
    except ValueError:
        continue
    if row.get("paid"):
        paid[row.get("order")] = row


def message_of(result):
    task = result.get("task") if isinstance(result.get("task"), dict) else result
    message = ((task or {}).get("status") or {}).get("message") or {}
    text = " ".join(p.get("text", "") for p in message.get("parts", []) if isinstance(p, dict))
    meta = dict(message.get("metadata") or {}) if isinstance(message.get("metadata"), dict) else {}
    return text, meta, ((task or {}).get("status") or {}).get("state")


table = defaultdict(Counter)
for name in sorted(n for n in os.listdir(ROOT / "logs/market-buyer") if "20261103T120000" <= n < "20261105T060000"):
    rec = json.loads((ROOT / "logs/market-buyer" / name).read_text(encoding="utf-8"))["record"]
    pay = paid.get(rec.get("order"))
    if not pay or not rec.get("buyer_log_dir"):
        continue
    folder = Path(rec["buyer_log_dir"].replace("\\", "/")).name
    seen = {}
    for path in sorted(glob.glob(str(ROOT / "logs/buyer" / folder / "*.response.txt"))):
        try:
            result = json.loads(Path(path).read_text(encoding="utf-8", errors="replace")).get("result") or {}
        except ValueError:
            continue
        text, meta, state = message_of(result)
        if state == "TASK_STATE_INPUT_REQUIRED":
            price, code, how = fast.read_ask(text, meta, BOTH)
            if how and code == "single_ask":
                seen[how] = price  # The last ask the seller stated in this deal.
    for how, price in seen.items():
        offers = [s.get("price") for s in rec.get("decision_trail") or [] if isinstance(s, dict) and s.get("price")]
        final = pay.get("price")
        table[how]["deals"] += 1
        if price == final:
            table[how]["last_ask_equals_settled"] += 1
        elif final in offers and price > final:
            table[how]["we_offered_lower_and_it_closed_there"] += 1
        elif price < final:
            table[how]["read_below_settled_unexplained"] += 1
        else:
            table[how]["read_above_settled_unexplained"] += 1
for how in ("metadata_ask", "my_price", "final_price", "lowest_price", "colon_krw"):
    print(how, dict(table[how]))
