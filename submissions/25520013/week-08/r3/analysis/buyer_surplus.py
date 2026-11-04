"""Why is our buyer's surplus per deal low? Round 3 decomposition (stdlib only, no network).

Usage (from r3/):
    uv run --no-project python analysis/buyer_surplus.py --data DIR [--out FILE]

DIR is a read-only copy of the live week-09 folder with these relative paths:
    .r3/events.jsonl        ledger (settle/deal = our buys, settle/market = every buyer's settles,
                            decision/hook buy:<order> = our attempt log, reject/payments, broken/*)
    .market/join.out        kit console; '[order o-..] <item>, budget B, round 3[, use within H h]' lines
                            are the only complete list of orders our buyer received (failed ones included)
    .market/catalog.json    sku -> list_price, kind, category
    .market/demand.json     item (category leaf) -> category, kind
    .market/payments.jsonl  our checkout refusals with the shelf-life hours in the reason text

Measured: everything read from the ledger / kit log. Inferred (flagged in the output): order list price
for orders without any candidate (budget / median budget-to-list ratio), and the counterfactuals.
Deterministic; no sampling (SEED is printed for the record only).
"""

import argparse
import collections
import json
import re
import statistics as st
from pathlib import Path

SEED = 20261104
ME = "lee-sungsoo"
ORDER_RX = re.compile(
    r"\[order (o-[0-9a-f]+)\] (.+?), budget (\d+), round 3(?:, use within ([\d.]+) h)?"
)


def jsonl(path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
            except ValueError:  # half-written last line
                continue


def q(xs, n=4):
    xs = sorted(xs)
    return [round(x) for x in st.quantiles(xs, n=n)] if len(xs) >= n else xs


def mean(xs, nd=0):
    xs = list(xs)
    return round(st.mean(xs), nd) if xs else None


def median(xs):
    xs = list(xs)
    return st.median(xs) if xs else None


def load(data):
    d = Path(data)
    cat = json.loads((d / ".market/catalog.json").read_text(encoding="utf-8"))
    dem = {
        x["item"]: x
        for x in json.loads((d / ".market/demand.json").read_text(encoding="utf-8"))
    }
    orders = {}
    for line in (d / ".market/join.out").read_text(encoding="utf-8").splitlines():
        m = ORDER_RX.search(line)
        if m:
            o, item, b, uwh = m.groups()
            info = dem.get(item) or {}
            orders[o] = {
                "order": o,
                "item": item,
                "budget": int(b),
                "uwh": float(uwh) if uwh else None,
                "category": info.get("category"),
                "kind": info.get("kind") or ("food" if uwh else None),
            }
    deals, decisions, rejects, a2a, market, broken_mkt, broken_mine = (
        {},
        {},
        collections.defaultdict(list),
        collections.Counter(),
        [],
        [],
        [],
    )
    for e in jsonl(d / ".r3/events.jsonl"):
        t, s, x = e.get("type"), e.get("src"), e.get("data") or {}
        if t == "settle" and s == "deal":
            deals[x["order"]] = dict(x, ts=e["ts"])
        elif t == "settle" and s == "market":
            market.append(dict(x, ts=e["ts"]))
        elif t == "decision" and s == "hook" and x.get("what") == "offer_selection":
            decisions[e["key"].split(":", 1)[1]] = x
        elif t == "reject" and s == "payments":
            rejects[x["order"]].append(x.get("reason_code"))
        elif t == "fail" and s == "hook" and x.get("stage") == "a2a":
            a2a[x["order"]] += 1
        elif t == "broken" and s == "market":
            broken_mkt.append(x)
        elif t == "broken" and s == "broken":
            broken_mine.append(x)
    return (
        cat,
        dem,
        orders,
        deals,
        decisions,
        rejects,
        a2a,
        market,
        broken_mkt,
        broken_mine,
    )


def deal_rows(cat, deals, broken_mine, broken_mkt):
    """Our buys with buyer value: list price, 0 if food did not last use_within_hours or goods broke."""
    broke = {x.get("order") for x in broken_mine if x.get("order")}
    broke_sku = collections.Counter(
        x["sku"] for x in broken_mkt if x.get("buyer") == ME
    )
    rows = []
    for o, x in deals.items():
        c = cat.get(x["sku"]) or {}
        lp = int(x.get("value") or c.get("list_price") or 0)
        kind = c.get("kind") or ("food" if x.get("expires_at") else "goods")
        spoiled = bool(
            kind == "food"
            and x.get("expires_at")
            and x.get("use_within_hours") is not None
            and x["expires_at"] < x["ts"] + 3600 * float(x["use_within_hours"])
        )
        is_broken = o in broke
        if not is_broken and broke_sku.get(
            x["sku"]
        ):  # market broken row without order id: match by sku once
            broke_sku[x["sku"]] -= 1
            is_broken = kind == "goods"
        value = 0 if (spoiled or is_broken) else lp
        rows.append(
            {
                "order": o,
                "sku": x["sku"],
                "kind": kind,
                "category": c.get("category") or x.get("category"),
                "list": lp,
                "price": int(x["amount"]),
                "value": value,
                "spoiled": spoiled,
                "broken": is_broken,
                "budget": x.get("budget"),
                "surplus": value - int(x["amount"]),
            }
        )
    return rows


def decompose(rows):
    n = len(rows)
    if not n:
        return {"deals": 0}
    lp = sum(r["list"] for r in rows)
    pr = sum(r["price"] for r in rows)
    loss = sum(r["list"] - r["value"] for r in rows)
    return {
        "deals": n,
        "surplus_total": sum(r["surplus"] for r in rows),
        "surplus_per_deal": round(sum(r["surplus"] for r in rows) / n),
        "mean_list": round(lp / n),
        "mean_price": round(pr / n),
        "price_over_list_weighted": round(pr / lp, 3) if lp else None,
        "price_over_list_mean": round(
            st.mean(r["price"] / r["list"] for r in rows if r["list"]), 3
        ),
        "loss_spoiled_broken_per_deal": round(loss / n),
        "n_spoiled": sum(r["spoiled"] for r in rows),
        "n_broken": sum(r["broken"] for r in rows),
        "refund_per_deal": 0,
    }


def outcome_of(o, decisions, deals, rejects, a2a):
    """Why an order did or did not complete (first matching reason)."""
    if o in deals:
        return "deal"
    d = decisions.get(o)
    if d is None:
        return "no_decision_logged"
    if d["candidates"] == 0:
        return "no_candidate_shop"
    if rejects.get(o):
        return "checkout_refused_by_us:" + rejects[o][-1]
    states = [
        t.get("state") or ("a2a_error" if t.get("error") else None) for t in d["tried"]
    ]
    if any(s == "TASK_STATE_COMPLETED" for s in states):
        return "receipt_but_not_settled"
    if any(s == "receipt_refused" for s in states):
        return "receipt_refused_by_us(cap/claim)"
    if any(s == "walked_away" for s in states):
        return "walked_away_stated_shelf/grade"
    if any(
        s == "TASK_STATE_REJECTED" and (t.get("rounds") or 0) > 0
        for s, t in zip(states, d["tried"])
    ):
        return "shop_rejected_after_haggle"
    if any(s == "TASK_STATE_INPUT_REQUIRED" for s in states):
        return "haggle_unfinished(cap/deadline)"
    if any(s == "TASK_STATE_REJECTED" for s in states):
        return "shop_rejected_at_round0"
    if any(s == "a2a_error" for s in states) or a2a.get(o):
        return "a2a_error"
    return "other:" + ",".join(str(s) for s in states)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument(
        "--out", default=str(Path(__file__).with_name("buyer_surplus_20261104.json"))
    )
    a = ap.parse_args()
    print(f"seed {SEED} (no stochastic step)")
    (
        cat,
        dem,
        orders,
        deals,
        decisions,
        rejects,
        a2a,
        market,
        broken_mkt,
        broken_mine,
    ) = load(a.data)
    out = {
        "seed": SEED,
        "inputs": {
            "orders_round3": len(orders),
            "our_deals": len(deals),
            "decisions": len(decisions),
            "market_settles": len(market),
        },
    }

    # 1. decomposition
    rows = deal_rows(cat, deals, broken_mine, broken_mkt)
    by_kind = collections.defaultdict(list)
    by_cat = collections.defaultdict(list)
    for r in rows:
        by_kind[r["kind"]].append(r)
        by_cat[r["category"]].append(r)
    top = sorted(by_cat, key=lambda k: -sum(r["surplus"] for r in by_cat[k]))[:10]
    out["q1_decomposition"] = {
        "all": decompose(rows),
        "by_kind": {k: decompose(v) for k, v in by_kind.items()},
        "top_categories_by_surplus": {k: decompose(by_cat[k]) for k in top},
        "top_categories_by_count": {
            k: decompose(by_cat[k])
            for k in sorted(by_cat, key=lambda k: -len(by_cat[k]))[:8]
        },
    }

    # budget / list ratio (from our deals) to estimate the list price of orders we never saw a candidate for
    ratio = median(r["budget"] / r["list"] for r in rows if r["budget"] and r["list"])
    out["budget_over_list_median_from_deals"] = round(ratio, 3)

    # 2. order completion
    orows = []
    for o, x in orders.items():
        d = decisions.get(o) or {}
        lps = [
            (cat.get(c["sku"]) or {}).get("list_price") for c in d.get("ranked") or []
        ]
        lps = [v for v in lps if v]
        est = (
            deals[o]["value"]
            if o in deals and deals[o].get("value")
            else (max(lps) if lps else None)
        )
        orows.append(
            dict(
                x,
                outcome=outcome_of(o, decisions, deals, rejects, a2a),
                est_list=est or round(x["budget"] / ratio),
                est_list_src="candidate" if est else "budget",
            )
        )
    bq = q([r["budget"] for r in orows])
    band = lambda b: (
        "Q1" if b <= bq[0] else "Q2" if b <= bq[1] else "Q3" if b <= bq[2] else "Q4"
    )

    def comp(rs):
        n = len(rs)
        k = sum(r["outcome"] == "deal" for r in rs)
        return {
            "orders": n,
            "deals": k,
            "completion": round(k / n, 3) if n else None,
            "mean_budget": mean(r["budget"] for r in rs),
            "reasons": dict(
                collections.Counter(r["outcome"] for r in rs).most_common()
            ),
        }

    oc = collections.defaultdict(list)
    for r in orows:
        oc[r["category"]].append(r)
    out["q2_completion"] = {
        "all": comp(orows),
        "by_kind": {
            k: comp([r for r in orows if r["kind"] == k]) for k in ("food", "goods")
        },
        "budget_quartile_edges": bq,
        "by_budget_quartile": {
            b: comp([r for r in orows if band(r["budget"]) == b])
            for b in ("Q1", "Q2", "Q3", "Q4")
        },
        "by_kind_x_quartile": {
            f"{k}/{b}": comp(
                [r for r in orows if r["kind"] == k and band(r["budget"]) == b]
            )
            for k in ("food", "goods")
            for b in ("Q1", "Q2", "Q3", "Q4")
        },
        "top_order_categories": {
            k: comp(oc[k]) for k in sorted(oc, key=lambda k: -len(oc[k]))[:12]
        },
        "goods_categories": {
            k: comp(oc[k])
            for k in sorted(oc, key=lambda k: -len(oc[k]))
            if oc[k] and oc[k][0]["kind"] == "goods"
        },
        "mean_budget_orders_vs_deals": {
            "all_orders": mean(r["budget"] for r in orows),
            "completed": mean(r["budget"] for r in orows if r["outcome"] == "deal"),
            "failed": mean(r["budget"] for r in orows if r["outcome"] != "deal"),
        },
    }
    # tried-attempt states (what happened at each shop we tried)
    tried = collections.Counter()
    r0 = collections.Counter()
    for d in decisions.values():
        for t in d["tried"]:
            s = t.get("state") or ("error:" + str(t.get("error")))
            tried[
                s
                if s != "walked_away"
                else "walked_away:" + re.sub(r"[\d.]+", "N", t.get("why") or "")
            ] += 1
            if s == "TASK_STATE_REJECTED" and not t.get("rounds"):
                r0[t.get("shop")] += 1
    out["q2_attempt_states"] = dict(tried.most_common(15))
    # shelf-life refusals: how many were inside hours_margin (unit lasts use_within_hours, misses the +margin)?
    margin = collections.Counter()
    for d in decisions.values():
        need = (d.get("accept") or {}).get("min_hours_left")
        for t in d["tried"]:
            m = re.search(r"stated ([\d.]+) h", t.get("why") or "")
            if m and need:
                margin[
                    "walk_within_margin"
                    if float(m.group(1)) >= need - 2
                    else "walk_below_use_within"
                ] += 1
    for p in jsonl(Path(a.data) / ".market/payments.jsonl"):
        m = re.search(
            r"([\d.]+) h of shelf life left, the policy asks for ([\d.]+)",
            p.get("reason") or "",
        )
        if m:
            left, need = map(float, m.groups())
            margin[
                "checkout_within_margin"
                if left >= need - 2
                else "checkout_below_use_within"
            ] += 1
    out["q2_shelf_refusals_vs_margin(assumes hours_margin=2)"] = dict(margin)
    # haggle: rounds of completed attempts and price/list of our deals
    rounds = collections.Counter(
        t.get("rounds")
        for d in decisions.values()
        for t in d["tried"]
        if t.get("state") == "TASK_STATE_COMPLETED"
    )
    out["q5_haggle"] = {
        "completed_attempt_rounds": dict(
            sorted(rounds.items(), key=lambda kv: str(kv[0]))
        ),
        "price_over_list_deciles_food": [
            round(x, 2)
            for x in st.quantiles(
                [r["price"] / r["list"] for r in rows if r["kind"] == "food"], n=10
            )
        ],
        "price_over_list_deciles_goods": [
            round(x, 2)
            for x in st.quantiles(
                [r["price"] / r["list"] for r in rows if r["kind"] == "goods"], n=10
            )
        ],
    }
    out["q2_round0_rejects_by_shop_top10"] = dict(r0.most_common(10))

    # 3. our price vs market median settle price per sku (other buyers)
    by_sku = collections.defaultdict(list)
    for m in market:
        if m.get("buyer") != ME:
            by_sku[m["sku"]].append(m["amount"])
    rel = [
        (r["price"] / median(by_sku[r["sku"]]), r["kind"])
        for r in rows
        if len(by_sku.get(r["sku"], [])) >= 3
    ]
    out["q3_price_vs_market_median"] = {
        "deals_with_>=3_other_settles": len(rel),
        "median_ratio": round(median(x for x, _ in rel), 3),
        "mean_ratio": round(st.mean(x for x, _ in rel), 3),
        "share_above_median": round(sum(x > 1 for x, _ in rel) / len(rel), 3),
        "by_kind": {
            k: round(median(x for x, kk in rel if kk == k), 3)
            for k in ("food", "goods")
            if any(kk == k for _, kk in rel)
        },
        "overpay_vs_median_per_deal": round(
            sum(
                r["price"] - median(by_sku[r["sku"]])
                for r in rows
                if len(by_sku.get(r["sku"], [])) >= 3
            )
            / len(rows)
        ),
    }

    # 4. benchmark: every buyer's settles (market feed); value = list price (breakage ignored here)
    bb = collections.defaultdict(list)
    for m in market:
        c = cat.get(m["sku"]) or {}
        if c.get("list_price"):
            bb[m["buyer"]].append(
                (c["list_price"], m["amount"], c.get("kind"), c.get("category"))
            )
    bench = {}
    for b, xs in bb.items():
        if len(xs) < 500:
            continue
        lp = sum(x[0] for x in xs)
        g = [x for x in xs if x[2] == "goods"]
        f = [x for x in xs if x[2] == "food"]
        bench[b] = {
            "deals": len(xs),
            "mean_list": round(lp / len(xs)),
            "price_over_list": round(sum(x[1] for x in xs) / lp, 3),
            "surplus_per_deal_list_based": round(
                (lp - sum(x[1] for x in xs)) / len(xs)
            ),
            "goods_share": round(len(g) / len(xs), 3),
            "goods_mean_list": mean((x[0] for x in g)),
            "food_mean_list": mean((x[0] for x in f)),
            "goods_price_over_list": round(
                sum(x[1] for x in g) / sum(x[0] for x in g), 3
            )
            if g
            else None,
            "food_price_over_list": round(
                sum(x[1] for x in f) / sum(x[0] for x in f), 3
            )
            if f
            else None,
        }
    out["q4_benchmark_buyers"] = dict(
        sorted(bench.items(), key=lambda kv: -kv[1]["surplus_per_deal_list_based"])
    )

    # 4. counterfactuals (inferred)
    me = out["q1_decomposition"]["all"]
    n, spd = me["deals"], me["surplus_per_deal"]
    g, f = by_kind.get("goods", []), by_kind.get("food", [])
    go = [r for r in orows if r["kind"] == "goods"]
    fo = [r for r in orows if r["kind"] == "food"]
    c_g = sum(r["outcome"] == "deal" for r in go) / len(go)
    c_f = sum(r["outcome"] == "deal" for r in fo) / len(fo)
    spd_g = sum(r["surplus"] for r in g) / len(g) if g else 0
    spd_f = sum(r["surplus"] for r in f) / len(f) if f else 0
    extra_g = len(go) * c_f - len(g)  # goods deals if goods completed like food
    h1 = (sum(r["surplus"] for r in rows) + max(0, extra_g) * spd_g) / (
        n + max(0, extra_g)
    )
    lead = out["q4_benchmark_buyers"].get("inextro", {})
    h2_pol = lead.get("price_over_list", me["price_over_list_weighted"])
    h2 = round(me["mean_list"] * (1 - h2_pol) - me["loss_spoiled_broken_per_deal"])
    h3 = spd + me["loss_spoiled_broken_per_deal"]
    h4 = round(lead.get("mean_list", 0) * (1 - me["price_over_list_weighted"]))
    # H2 per kind: our deals re-priced at the leader's price/list for the same kind
    h2k = (
        sum(
            r["list"]
            * (
                1
                - (
                    lead.get(f"{r['kind']}_price_over_list")
                    or me["price_over_list_weighted"]
                )
            )
            - (r["list"] - r["value"])
            for r in rows
        )
        / n
    )
    # order mix over time (first vs second half of the orders in arrival order)
    seq = list(orders.values())
    half = len(seq) // 2
    mix = {
        f"half{i + 1}": round(sum(r["kind"] == "goods" for r in part) / len(part), 3)
        for i, part in enumerate((seq[:half], seq[half:]))
    }
    # recoverable surplus (inferred): failed orders x surplus/deal of completed orders of the same kind and band
    spd_kb = {}
    for r in orows:
        if r["outcome"] == "deal":
            spd_kb.setdefault((r["kind"], band(r["budget"])), []).append(
                next(x["surplus"] for x in rows if x["order"] == r["order"])
            )
    rec = collections.Counter()
    for r in orows:
        if r["outcome"] != "deal":
            v = spd_kb.get((r["kind"], band(r["budget"])))
            rec[
                (
                    r["kind"],
                    r["outcome"].split(":")[0]
                    if "checkout" not in r["outcome"]
                    else r["outcome"],
                )
            ] += st.mean(v) if v else 0
    total = sum(r["surplus"] for r in rows)
    out["q4_recoverable_if_failed_orders_completed_at_band_mean"] = {
        f"{k}/{why}": {
            "surplus": round(v),
            "pct_of_current_total": round(100 * v / total, 1),
        }
        for (k, why), v in sorted(rec.items(), key=lambda kv: -kv[1])[:10]
    }
    out["q4_hypotheses"] = {
        "order_goods_share_over_time": mix,
        "H2_per_kind_leader_price_over_list_surplus_per_deal": round(h2k),
        "now_surplus_per_deal": spd,
        "goods_completion": round(c_g, 3),
        "food_completion": round(c_f, 3),
        "goods_orders": len(go),
        "food_orders": len(fo),
        "goods_surplus_per_deal": round(spd_g),
        "food_surplus_per_deal": round(spd_f),
        "H1_goods_completion_equal_food_surplus_per_deal": round(h1),
        "H2_price_over_list_at_leader_level": {
            "leader_pol": h2_pol,
            "surplus_per_deal": h2,
        },
        "H3_no_spoil_or_break_surplus_per_deal": h3,
        "H4_leader_mean_list_at_our_price_share": h4,
        "note": "H1/H2/H4 are inferred counterfactuals; H4 is not reachable by policy since order categories are "
        "assigned by the market.",
    }
    Path(a.out).write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        json.dumps(
            {k: out[k] for k in ("inputs", "budget_over_list_median_from_deals")},
            ensure_ascii=False,
        )
    )
    print(
        json.dumps(out["q1_decomposition"]["all"]),
        json.dumps(out["q1_decomposition"]["by_kind"]),
    )
    print(
        json.dumps(
            {
                k: {
                    kk: v[kk] for kk in ("orders", "deals", "completion", "mean_budget")
                }
                for k, v in out["q2_completion"]["by_kind_x_quartile"].items()
            }
        )
    )
    print(json.dumps(out["q2_completion"]["all"], ensure_ascii=False))
    print(json.dumps(out["q3_price_vs_market_median"]))
    print(json.dumps(out["q4_hypotheses"], ensure_ascii=False))
    for k in ("q2_shelf_refusals_vs_margin(assumes hours_margin=2)", "q5_haggle",
              "q4_recoverable_if_failed_orders_completed_at_band_mean"):
        print(k, json.dumps(out[k], ensure_ascii=False))
    print("wrote", a.out)


if __name__ == "__main__":
    main()
