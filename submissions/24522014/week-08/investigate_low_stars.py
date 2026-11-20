"""Read-only look at whether low stars on our shop follow from the deal or from who gave them. Changes nothing in the market.

Only public rows are fetched, at one call a second: the ledger pages holding our own sales, known from local receipts. The
same pages hold other shops' reviews from the same hours, which are the baseline, so it is a sample of the market and not all
of it. Observed patterns cannot show intent, so the report only grades how much a pattern needs explaining.

Grades (buyers with at least MIN_OURS verified reviews of us and MIN_OTHERS of other shops):
  strong: mean on us is at least GAP below that buyer's own mean elsewhere, also for like deals (same kind, warranty and price
          band), the low share on us exceeds its share elsewhere with one-sided Fisher p < ALPHA / number of buyers tested
          (Bonferroni), low stars exceed what the buyer gave others in the same 6-hour windows by z >= Z_TIME, and at least half
          of its low reviews of us have no problem in the public or local record.
  weak:   the same gap on both comparisons, the timing check, and raw p < ALPHA, without the Bonferroni or unexplained tests.
  none:   everything else, with too-small samples labelled as such.
"""

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from math import comb
from pathlib import Path
import re
import ssl
import time

ROOT = Path(__file__).resolve().parent
BASE = "https://market.ourolabs.ai"
OWN = "kcwarni"
LOW = 2
MIN_OURS, MIN_OTHERS, GAP, ALPHA = 5, 10, 1.0, .05
Z_TIME, WINDOW_SECONDS, MIN_LIKE_DEALS, MIN_WINDOW_OTHERS = 3, 21600, 5, 10
SKU = re.compile(r"^G[0-9a-f]{8}$")
KEEP = ("settle", "result", "broken", "refund")


def own_sales(root):
    records = json.loads((Path(root) / ".market/local-settlements.json").read_text(encoding="utf-8")).get("records") or {}
    records = records.values() if isinstance(records, dict) else records
    return sorted(int(r["ledger"]) for r in records if r.get("source") == "local_acp_settled" and r.get("round") == 3 and r.get("ledger") is not None)


def fetch(root, out, pace=1.0, max_calls=1200, client=None, sleep=time.sleep):
    """Pages that begin at each own sale not yet covered, kept to the rows this report uses. Stops at the first refusal."""
    import httpx
    client = client or httpx.Client(verify=ssl.create_default_context(), timeout=30, trust_env=False)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    calls, covered, pages = 0, -1, []
    sales = own_sales(root)
    stop = None
    with (out / "rows.jsonl").open("a", encoding="utf-8") as stream:
        for seq in sales:
            if seq <= covered:
                continue
            if calls >= max_calls:
                stop = "max_calls"
                break
            response = client.get(BASE + "/api/ledger", params={"after": seq - 1})
            calls += 1
            if response.status_code != 200:
                stop = f"http_{response.status_code}"
                break
            rows = response.json()
            if not rows:
                continue
            for row in rows:
                if row.get("kind") in KEEP:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            covered = max(r["seq"] for r in rows)
            pages.append([seq, covered])
            sleep(pace)
        if stop is None:
            for path, name in ((f"/api/board", "board.json"), ("/api/shops", "shops.json")):
                response = client.get(BASE + path, params={"round": 3} if name == "board.json" else None)
                calls += 1
                if response.status_code == 200:
                    (out / name).write_text(response.text, encoding="utf-8")
                sleep(pace)
    meta = {"calls": calls, "pages": pages, "own_sales": len(sales), "last_sale_seq": sales[-1] if sales else None, "stopped": stop,
            "finished_at": datetime.now(timezone.utc).isoformat()}
    (out / "fetch.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def fisher_greater(a, b, c, d):
    """One-sided Fisher exact p that the first row's share of the first column is this high by chance. Table [[a, b], [c, d]]."""
    row, col, total = a + b, a + c, a + b + c + d
    denominator = comb(total, col)
    return sum(comb(row, k) * comb(total - row, col - k) for k in range(a, min(row, col) + 1)) / denominator if denominator else 1.0


def load_rows(*paths):
    seen = {}
    for path in paths:
        if not Path(path).exists():
            continue
        with Path(path).open(encoding="utf-8") as stream:
            for line in stream:
                row = json.loads(line)
                seen[row["seq"]] = row
    return [seen[key] for key in sorted(seen)]


def like_deal(row, settles, catalog):
    """Same kind, warranty or none, and price band. None when the sale row or list price is missing."""
    sale = settles.get(row["data"].get("order"))
    listed = (catalog.get(sale["data"].get("item")) or {}).get("list_price") if sale else None
    if not listed:
        return None
    share = sale["data"].get("amount", 0) / listed
    band = next((limit for limit in (.6, .7, .8, .9) if share < limit), 1)
    return (catalog[sale["data"]["item"]].get("kind"), bool(sale["data"].get("warranty_hours")), band)


def like_for_like(mine, other, settles, catalog):
    """Mean of (stars on us - this buyer's mean elsewhere for like deals), with how many of ours had a comparison."""
    table = defaultdict(list)
    for row in other:
        table[like_deal(row, settles, catalog)].append(row["data"]["stars"])
    gaps = []
    for row in mine:
        reference = table.get(like_deal(row, settles, catalog)) or []
        if len(reference) >= MIN_LIKE_DEALS:
            gaps.append(row["data"]["stars"] - sum(reference) / len(reference))
    return (round(sum(gaps) / len(gaps), 3) if gaps else None), len(gaps)


def time_matched(mine, other):
    """Low stars on us against the share this buyer gave others in the same windows. Windows with too few other reviews are left out."""
    windows = defaultdict(lambda: [0, 0, 0, 0])
    for row in mine:
        cell = windows[int(row["ts"] // WINDOW_SECONDS)]
        cell[0] += 1
        cell[1] += row["data"]["stars"] <= LOW
    for row in other:
        cell = windows[int(row["ts"] // WINDOW_SECONDS)]
        cell[2] += 1
        cell[3] += row["data"]["stars"] <= LOW
    expected = observed = variance = 0.0
    for ours_n, ours_low, others_n, others_low in windows.values():
        if ours_n and others_n >= MIN_WINDOW_OTHERS:
            share = others_low / others_n
            expected += ours_n * share
            observed += ours_low
            variance += ours_n * share * (1 - share)
    z = None
    if variance:
        z = round((observed - expected) / variance ** .5, 2)
    elif observed > expected:
        z = 99.0  # Low on us while this buyer gave others none: no spread to divide by, but clearly above.
    return {"expected_low": round(expected, 1), "observed_low": int(observed), "z": z}


def receipt_format(row):
    item = (row["data"].get("receipt") or {}).get("item")
    return "none" if item is None else "sku" if SKU.match(str(item)) else "name"


def by_receipt_format(verified, own_urls):
    """Buyers who reviewed at least 5 receipts of each format and whose means differ by 0.3 or more; only the format can explain it."""
    groups = defaultdict(lambda: defaultdict(list))
    for row in verified:
        groups[row["data"].get("buyer")][receipt_format(row)].append(row)
    found = []
    for buyer, formats in groups.items():
        sku, name = formats.get("sku", []), formats.get("name", [])
        if len(sku) >= 5 and len(name) >= 5:
            mean_sku, mean_name = (sum(r["data"]["stars"] for r in rows) / len(rows) for rows in (sku, name))
            if abs(mean_sku - mean_name) >= .3:
                found.append({"buyer": buyer, "sku": {"n": len(sku), "mean": round(mean_sku, 3), "low": sum(r["data"]["stars"] <= LOW for r in sku), "shops": len({r["data"]["shop"] for r in sku}),
                                                      "on_us": sum(r["data"]["shop"] in own_urls for r in sku)},
                              "name": {"n": len(name), "mean": round(mean_name, 3), "low": sum(r["data"]["stars"] <= LOW for r in name), "shops": len({r["data"]["shop"] for r in name}),
                                       "on_us": sum(r["data"]["shop"] in own_urls for r in name)}})
    return found


def all_shop_low_runs(by_buyer):
    """When a buyer's low stars reach most of every shop at once: the first of its windows with at least half low, and the share since."""
    runs = {}
    for buyer, value in by_buyer.items():
        rows = sorted(value["ours"] + value["others"], key=lambda r: r["ts"])
        windows = defaultdict(list)
        for row in rows:
            windows[int(row["ts"] // WINDOW_SECONDS)].append(row)
        onset = next((key for key in sorted(windows) if len(windows[key]) >= 10 and sum(r["data"]["stars"] <= LOW for r in windows[key]) * 2 >= len(windows[key])), None)
        if onset is None:
            continue
        later = [r for r in rows if int(r["ts"] // WINDOW_SECONDS) >= onset]
        low = [r for r in later if r["data"]["stars"] <= LOW]
        runs[buyer] = {"first_low_ts": low[0]["ts"] if low else None, "since_n": len(later), "since_low": len(low), "shops_since": len({r["data"]["shop"] for r in later}), "shops_with_low": len({r["data"]["shop"] for r in low})}
    return runs


def problems(sale, broken, refunds, catalog):
    """What the record shows went wrong with one of our deals. Empty means no recorded problem, not proof there was none."""
    data = sale["data"]
    found = []
    if data.get("order") in broken:
        found.append("broke")
    if data.get("order") in refunds:
        found.append("refund")
    if data.get("claim_honest") is False:
        found.append("claim_not_honest")
    if data.get("grade") == "C":
        found.append("grade_c")
    if data.get("grade") == "B":
        found.append("grade_b")
    listed = (catalog.get(data.get("item")) or {}).get("list_price")
    if listed and data.get("amount", 0) / listed >= .9:
        found.append("paid_90pct_of_list_or_more")
    return found


def review_mentions(text):
    lowered = str(text or "").lower()
    terms = {"price": r"price|expensive|overpric|비싸|가격|surplus", "grade": r"grade|등급|condition|quality|품질", "broken": r"broke|고장|fault|defect|warranty|보증",
             "shelf": r"expire|shelf|유통기한", "slow": r"slow|timeout|응답|느리", "dishonest": r"dishonest|mismatch|lied|거짓|사기|scam"}
    return sorted(key for key, pattern in terms.items() if re.search(pattern, lowered))


def analyze(rows, own_urls, catalog):
    settles = {r["data"].get("order"): r for r in rows if r["kind"] == "settle" and r["data"].get("order")}
    broken = {r["data"].get("order") for r in rows if r["kind"] == "broken"}
    refunds = {r["data"].get("order") for r in rows if r["kind"] == "refund"}
    verified = [r for r in rows if r["kind"] == "result" and r["data"].get("verified") and type(r["data"].get("stars")) in (int, float)]
    ours = [r for r in verified if r["data"].get("shop") in own_urls]
    others = [r for r in verified if r["data"].get("shop") and r["data"].get("shop") not in own_urls]
    unverified_ours = [r for r in rows if r["kind"] == "result" and not r["data"].get("verified") and r["data"].get("shop") in own_urls]
    by_buyer = defaultdict(lambda: {"ours": [], "others": []})
    for row in ours:
        by_buyer[row["data"].get("buyer")]["ours"].append(row)
    for row in others:
        if row["data"].get("buyer") in by_buyer:
            by_buyer[row["data"]["buyer"]]["others"].append(row)
    tested = [b for b, v in by_buyer.items() if len(v["ours"]) >= MIN_OURS and len(v["others"]) >= MIN_OTHERS]
    table, low_details = [], []
    for buyer, value in by_buyer.items():
        mine, other = value["ours"], value["others"]
        stars_mine = [r["data"]["stars"] for r in mine]
        stars_other = [r["data"]["stars"] for r in other]
        low_mine = [r for r in mine if r["data"]["stars"] <= LOW]
        unexplained = 0
        for row in low_mine:
            sale = settles.get(row["data"].get("order"))
            found = problems(sale, broken, refunds, catalog) if sale else ["sale_row_missing"]
            hard = [f for f in found if f in ("broke", "refund", "claim_not_honest", "grade_c", "paid_90pct_of_list_or_more", "sale_row_missing")]
            unexplained += not hard
            low_details.append({"buyer": buyer, "seq": row["seq"], "ts": row["ts"], "stars": row["data"]["stars"], "item": (sale or {"data": {}})["data"].get("item"),
                                "grade": (sale or {"data": {}})["data"].get("grade"), "paid": (sale or {"data": {}})["data"].get("amount"),
                                "list": (catalog.get((sale or {"data": {}})["data"].get("item")) or {}).get("list_price"), "record": found, "review_mentions": review_mentions(row["data"].get("review")),
                                "review": str(row["data"].get("review") or "")[:240]})
        low_other = sum(s <= LOW for s in stars_other)
        mean_mine = sum(stars_mine) / len(stars_mine)
        mean_other = sum(stars_other) / len(stars_other) if stars_other else None
        p = fisher_greater(len(low_mine), len(mine) - len(low_mine), low_other, len(other) - low_other) if other else None
        like_gap, like_n = like_for_like(mine, other, settles, catalog)
        timing = time_matched(mine, other)
        grade = "none"
        note = None
        if buyer in tested:
            gap_met = mean_other - mean_mine >= GAP and like_gap is not None and like_gap <= -GAP
            mostly_unexplained = bool(low_mine) and unexplained * 2 >= len(low_mine)
            timed = timing["z"] is not None and timing["z"] >= Z_TIME
            if gap_met and p < ALPHA / len(tested) and mostly_unexplained and timed:
                grade = "strong"
            elif gap_met and p < ALPHA and timed:
                grade = "weak"
        else:
            note = "sample_too_small"
        table.append({"buyer": buyer, "ours_n": len(mine), "ours_mean": round(mean_mine, 3), "ours_low": len(low_mine), "others_n": len(other), "others_mean": round(mean_other, 3) if mean_other is not None else None,
                      "others_low_share": round(low_other / len(other), 4) if other else None, "gap": round(mean_other - mean_mine, 3) if mean_other is not None else None, "fisher_p": round(p, 6) if p is not None else None,
                      "low_without_recorded_problem": unexplained, "like_for_like_gap": like_gap, "like_for_like_matched": like_n, "time_matched": timing, "grade": grade, "note": note, "star_counts": dict(sorted(Counter(stars_mine).items())), "distinct_other_shops": len({r["data"]["shop"] for r in other})})
    table.sort(key=lambda r: (-r["ours_low"], -r["ours_n"]))
    text = Counter(re.sub(r"\d[\d,.]*", "#", str(r["data"].get("review") or ""))[:80] for r in ours if r["data"]["stars"] <= LOW)
    stars_ours = [r["data"]["stars"] for r in ours]
    stars_others = [r["data"]["stars"] for r in others]
    return {"verified_reviews_of_us": len(ours), "unverified_results_naming_us": len(unverified_ours), "verified_reviews_of_others_in_sample": len(others), "buyers_tested": len(tested),
            "our_mean": round(sum(stars_ours) / len(stars_ours), 3) if stars_ours else None, "our_counts": dict(sorted(Counter(stars_ours).items())),
            "others_mean": round(sum(stars_others) / len(stars_others), 3) if stars_others else None, "others_counts": dict(sorted(Counter(stars_others).items())),
            "table": table, "low_reviews": low_details, "low_review_templates": text.most_common(15),
            "by_receipt_format": by_receipt_format(verified, own_urls), "low_runs_across_all_shops": all_shop_low_runs(by_buyer),
            "our_receipt_formats": dict(Counter(receipt_format(r) for r in ours)), "others_receipt_formats": dict(Counter(receipt_format(r) for r in others))}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("fetch", "report"))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--max-calls", type=int, default=1200)
    args = parser.parse_args()
    if args.command == "fetch":
        print(json.dumps({k: v for k, v in fetch(ROOT, args.out, max_calls=args.max_calls).items() if k != "pages"}))
        return
    registry = json.loads((args.out / "shops.json").read_text(encoding="utf-8"))
    shops = registry.get("shops", []) if isinstance(registry, dict) else registry
    own_urls = {s.get("url") for s in shops if str(s.get("name", "")).casefold() == OWN}
    rows = load_rows(args.out / "rows.jsonl", args.out / "rows-tail.jsonl")
    own_urls |= {r["data"].get("shop") for r in rows if r["kind"] == "settle" and r["data"].get("unit") in local_units(ROOT)}
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    print(json.dumps(analyze(rows, own_urls, catalog), ensure_ascii=False, indent=1))


def local_units(root):
    records = json.loads((Path(root) / ".market/local-settlements.json").read_text(encoding="utf-8")).get("records") or {}
    records = records.values() if isinstance(records, dict) else records
    return {r.get("unit") for r in records if r.get("unit")}


if __name__ == "__main__":
    main()
