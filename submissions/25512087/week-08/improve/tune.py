"""Propose and apply bounded policy changes from the latest metrics (week 08 self-improvement loop, step 3).

    python3 improve/tune.py [--dry-run]

Reads the two newest improve/metrics/*.json windows and improve/policy.json, applies at most one step per knob,
always inside tuning.BOUNDS, writes improve/policy.json and appends every change (with its evidence) to
improve/changes.jsonl. Rules are deliberately simple and readable; the human/Claude review in the 3-hourly report
decides on code changes, this script only moves numbers.

Rollback: the objective is shop margin + buyer surplus per hour of the window. If the previous cycle changed knobs
and the objective then fell by more than ROLLBACK_DROP with enough data, those knobs go back to their old values.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import tuning  # noqa: E402

DEFAULTS = {
    "seller": {"ask_of_list": 0.92, "min_margin": 0.05, "max_rounds": 6, "food_markdown_h": 2.0, "food_markdown_share": 0.5, "endgame_h": 24, "endgame_floor": 0.8},
    "restock": {"CASH_FLOOR": 0.30, "PER_SKU_CAP": 0.05, "OPENING_BUDGET": 0.20, "TARGET_CATEGORIES": 60, "HOT_LINES": 3,
                "MAX_LIST_PRICE": 3_000_000, "HOT_QTY": 3, "REORDER_MULT": 1.5, "FOOD_MAX": 4},
    "buyer": {"rung_1": 0.50, "rung_step": 0.10, "max_share": 0.92, "est_price": 0.70, "near_best": 0.80, "shelf_strikes": 2},
}
ROLLBACK_DROP = 0.40
MIN_EVENTS = 5


def load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def objective(m: dict) -> tuple[float | None, int]:
    """(margin + surplus) per hour in the window, and how many events it rests on."""
    h = m.get("window_hours") or 0
    shop, buy = m["window"]["shop"], m["window"]["buyer"].get("payments") or {}
    n = shop.get("settled", 0) + buy.get("paid", 0)
    if h <= 0.25:
        return None, n
    return (shop.get("margin", 0) + buy.get("surplus", 0)) / h, n


def propose(m: dict, pol: dict) -> list[dict]:
    w, now = m["window"], m["shop_now"]
    shop, buy = w["shop"], w["buyer"]
    pay = buy.get("payments") or {}
    h = max(0.25, m.get("window_hours") or 3)
    out = []

    def step(sec, key, delta, why):
        old = pol[sec][key]
        new = tuning.clamp(sec, key, old + delta)
        if new != old:
            out.append({"section": sec, "key": key, "old": old, "new": new, "why": why})

    # seller: price against how readily buyers sign
    if shop.get("openings", 0) >= MIN_EVENTS and shop.get("accept_rate") is not None:
        if shop["accept_rate"] >= 0.80:
            step("seller", "ask_of_list", +0.02, f"{shop['accept_rate']:.0%} of {shop['openings']} talks signed: room to ask more")
        elif shop["accept_rate"] < 0.40:
            step("seller", "ask_of_list", -0.03, f"only {shop['accept_rate']:.0%} of {shop['openings']} talks signed")
    # restock: traffic vs coverage, cash vs stock
    per_h = shop.get("openings", 0) / h
    if per_h < 10 and now.get("categories", 0) >= pol["restock"]["TARGET_CATEGORIES"] * 0.9 \
            and now.get("balance", 0) > 0.5 * 5_000_000:
        step("restock", "TARGET_CATEGORIES", +20, f"{per_h:.1f} buyers/h reach us with {now['categories']} categories in stock")
        step("restock", "OPENING_BUDGET", +0.05, "more categories need more opening stock")
    if now.get("goods_older_3d", 0) > 0.3 * max(1, now.get("stock_units", 0)):
        step("restock", "OPENING_BUDGET", -0.05, f"{now['goods_older_3d']} goods units older than 3 days")
    if now.get("net_profit", 0) < -0.4 * 5_000_000:
        step("restock", "CASH_FLOOR", +0.05, f"balance down {-now['net_profit']:,} from the start")
    # buyer: ladder against walks and price paid
    talks = buy.get("talks", 0)
    walks = sum(v for k, v in (buy.get("outcomes") or {}).items() if k.startswith("value:"))
    if talks >= MIN_EVENTS and walks / talks > 0.20:
        step("buyer", "max_share", +0.02, f"{walks}/{talks} talks walked at our value cap")
    # 11/04 (approved): the rung_1 rule is retired. It proposed the same cut every cycle; two cuts left the share of
    # value we pay flat (73-76 %): shops' floors set the price, not our first rung.
    # New rule: the kit's refusal share (signed, then not paid: short shelf life or no certificate).
    signed = pay.get("paid", 0) + pay.get("refused", 0)
    if signed >= 2 * MIN_EVENTS:
        share = pay.get("refused", 0) / signed
        if share > 0.25:
            step("buyer", "shelf_strikes", -1, f"{pay['refused']}/{signed} signed deals refused by the kit: skip short-dated shops sooner")
        elif share < 0.10:
            step("buyer", "shelf_strikes", +1, f"only {pay['refused']}/{signed} refused: give shops more chances")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    files = sorted(p for p in (HERE / "metrics").glob("2*.json"))
    if not files:
        sys.exit("no metrics yet: run improve/measure.py first")
    cur = load(files[-1], {})
    prev = load(files[-2], {}) if len(files) > 1 else None
    pol = {sec: dict(DEFAULTS[sec], **tuning_section(sec)) for sec in DEFAULTS}
    changes = []
    log = HERE / "changes.jsonl"
    history = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()] if log.exists() else []
    last = [c for c in history if c.get("cycle") == (history[-1]["cycle"] if history else None) and not c.get("rollback")]
    o_cur, n_cur = objective(cur)
    o_prev, n_prev = objective(prev) if prev else (None, 0)
    if last and o_cur is not None and o_prev and o_prev > 0 and n_cur >= MIN_EVENTS and n_prev >= MIN_EVENTS \
            and o_cur < (1 - ROLLBACK_DROP) * o_prev:
        for c in last:
            changes.append({"section": c["section"], "key": c["key"], "old": pol[c["section"]][c["key"]], "new": c["old"],
                            "why": f"rollback: objective {o_prev:,.0f}/h -> {o_cur:,.0f}/h after this change", "rollback": True})
    elif (cur.get("window_hours") or 0) >= 1:          # a window under an hour is too thin to act on
        changes = propose(cur, pol)
    cycle = cur.get("kst")
    for c in changes:
        c.update(cycle=cycle, ts=round(time.time()), objective_per_h=o_cur and round(o_cur))
        pol[c["section"]][c["key"]] = c["new"]
    print(json.dumps({"cycle": cycle, "objective_per_h": o_cur and round(o_cur), "previous_per_h": o_prev and round(o_prev),
                      "changes": changes, "policy": pol, "dry_run": a.dry_run}, ensure_ascii=False, indent=1))
    if a.dry_run or not changes:
        return
    tuning.POLICY_FILE.write_text(json.dumps(pol, ensure_ascii=False, indent=1), encoding="utf-8")
    with log.open("a", encoding="utf-8") as f:
        for c in changes:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")


def tuning_section(sec: str) -> dict:
    return tuning.load(sec)


if __name__ == "__main__":
    main()
