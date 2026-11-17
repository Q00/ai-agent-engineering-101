"""Counts the kit's buyer payment results in .market/payments.jsonl by reason family (read only)."""
import collections
import json
import re
import sys

FAMILIES = [
    ("shelf_life", r"^not paying: .* h of shelf life left"),
    ("grade", r"^not paying: grade "),
    ("certificate", r"^not paying: no certificate"),
    ("not_in_stock_409", r"^checkout refused: 409 .*not in stock"),
    ("checkout_other", r"^checkout refused: "),
    ("market_402", r"^payment not completed: 402"),
    ("acp_endpoint_host", r"^the ACP endpoint .* is not on the shop's"),
    ("no_acp_json", r"serves no /\.well-known/acp\.json"),
    ("network", r"^payment failed: "),
]

path = sys.argv[1]
rows = paid = 0
fam = collections.Counter()
checks = collections.Counter()
first = last = None
for line in open(path, encoding="utf-8"):
    try:
        r = json.loads(line)
    except ValueError:
        continue
    rows += 1
    if r.get("paid"):
        paid += 1
        continue
    s = str(r.get("reason", ""))
    for name, pat in FAMILIES:
        if re.search(pat, s):
            fam[name] += 1
            break
    else:
        fam["other"] += 1
    m = re.search(r"failed_checks=([a-z_0-9,]+)", s)
    if m:
        for c in m.group(1).split(","):
            checks[c] += 1
    m = re.search(r'"checks":(\{[^}]*\})', s)
    if m:
        try:
            for k, v in json.loads(m.group(1)).items():
                if v is False:
                    checks[k] += 1
        except ValueError:
            checks["unparsed"] += 1
print(f"rows {rows}, paid {paid}, not paid {rows - paid}")
for k, v in fam.most_common():
    print(f"  {k:20s} {v}")
print("market 402 failed checks:", dict(checks.most_common()))
