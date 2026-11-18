"""Offline check of prices_r3.py: going prices from a fake ledger, forum price claims checked against it.

    python test_prices_r3.py      # prints PASS/FAIL per case; save with | tee logs/prices-r3-test-<time>.txt
"""
import json
import tempfile
import time
from pathlib import Path

import prices_r3

NOW = time.time()
CAT = {"G1": {"list_price": 100000, "category": "가구/침구>침구>패드", "name": "Pad"},
       "G2": {"list_price": 50000, "category": "가구/침구>침대>스프링 매트리스", "name": "Spring"},
       "G3": {"list_price": 20000, "category": "식품>즉석>스프", "name": "Soup"}}
LEDGER = ([{"kind": "settle", "ts": NOW - 600 * i, "data": {"item": "G1", "amount": a}} for i, a in enumerate((70000, 80000, 90000))]
          + [{"kind": "settle", "ts": NOW - 30 * 3600, "data": {"item": "G1", "amount": 10000}}]        # older than 24 h
          + [{"kind": "settle", "ts": NOW - 8 * 3600, "data": {"item": "G2", "amount": 30000}}])        # 24 h but not 6 h
FORUM = [{"id": 1, "ts": NOW - 60, "author": "honest", "role": "buyer", "text": "[시세] 패드: 정산 3건, 정가 대비 중간값 79%"},
         {"id": 2, "ts": NOW - 60, "author": "pumper", "role": "shop", "text": "price index 패드 median 120% of list!"},
         {"id": 3, "ts": NOW - 60, "author": "x", "role": "buyer", "text": "price: 스프링 매트리스 70% of list"},
         {"id": 4, "ts": NOW - 60, "author": "y", "role": "buyer", "text": "패드 restocked, 50% off coupons"}]


def main():
    md = Path(tempfile.mkdtemp(prefix="prices-"))
    (md / "catalog.json").write_text(json.dumps(CAT, ensure_ascii=False), encoding="utf-8")
    (md / "ledger-recent.json").write_text(json.dumps(LEDGER), encoding="utf-8")
    (md / "forum.json").write_text(json.dumps(FORUM, ensure_ascii=False), encoding="utf-8")
    out = prices_r3.write(md)
    ok = []

    def check(name, cond, detail):
        ok.append(bool(cond))
        print(f"{'PASS' if cond else 'FAIL'}  {name}: {detail}", flush=True)

    pad = out["categories"].get("패드")
    check("category median from settles of the last 24 h only", pad and pad["h24"]["n"] == 3 and pad["h24"]["median"] == 0.8, pad)
    sm = out["categories"].get("스프링 매트리스")
    check("a settle 8 h ago counts for 24 h, not 6 h", sm and sm["h24"]["n"] == 1 and sm["h6"] is None, sm)
    claims = {(c["author"], c["category"]): c for c in out["forum"]}
    check("forum price claims are checked against the ledger", claims[("honest", "패드")]["gap"] == -0.01
          and claims[("pumper", "패드")]["gap"] == 0.4, claims)
    check("a category name inside a longer word is not a claim (스프 in 스프링), a post with no price words is skipped",
          ("x", "스프") not in claims and ("x", "스프링 매트리스") in claims and not any(a == "y" for a, _ in claims), sorted(claims))
    check("per author, the mean gap where the ledger has 3+ settles", out["authors"].get("pumper") == {"claims": 1, "mean_gap": 0.4}
          and "x" not in out["authors"], out["authors"])
    line = prices_r3.line(md, "G1", CAT["G1"]["category"])
    check("one line for a model: SKU, category and the forum, the ledger first", "this SKU settled 3 times" in line
          and "median 80% of list" in line and "forum posts claim" in line, line)
    print(f"\n{sum(ok)}/{len(ok)} passed")


if __name__ == "__main__":
    main()
