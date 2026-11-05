"""Read-only: the ten-second interval over three named windows, with the 17:00 tools, plus the market around them. No network, no model.

Windows are KST. The third starts at the first shop-list reading after the last one under 45 shops. Also the strategy experiment's reach into buying.
Run from week-08 with --since3, --until3 and --out; prints the same JSON. Holds no costs or floors.
"""

import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))
import buyer_pace_detail_20261105 as detail  # noqa: E402
import buyer_pace_market_20261105 as market  # noqa: E402
from measure_buyer_pace import measure  # noqa: E402

KST = detail.KST
ROOT = detail.ROOT


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=KST)


def market_in(dated, start, end):
    ledger, shops, empty = [], [], 0
    for moment, raw in dated:
        if not start <= moment < end:
            continue
        found = market.LEDGER.search(raw)
        if found:
            ledger.append((moment, int(found[1])))
        found = market.SHOPS.search(raw)
        if found:
            shops.append(int(found[1]))
        empty += b"no shops in the market yet" in raw
    span = (ledger[-1][0]-ledger[0][0]).total_seconds() if len(ledger) > 1 else 0
    return {"ledger_rows_per_hour_all_market": round((ledger[-1][1]-ledger[0][1])/span*3600) if span else None, "shop_readings": len(shops), "shops_min": min(shops) if shops else None, "shops_max": max(shops) if shops else None, "empty_list_lines": empty}


def experiment_reach(rows, start, end, payments):
    state = json.loads((ROOT / ".market/strategy-state.json").read_text(encoding="utf-8"))
    active = state.get("active") or {}
    sku = active.get("sku")
    picked = [r for moment, r in rows if start <= moment < end and r.get("item") == sku]
    return {"id": active.get("id"), "kind": active.get("kind"), "phase": active.get("phase"), "sku": sku, "candidate": active.get("candidate"), "started_at_kst": datetime.fromtimestamp(active["started_at"], KST).isoformat() if active.get("started_at") else None,
            "our_buy_trades_of_that_sku_in_third_window": len(picked), "paid": sum(bool((payments.get(r.get("order")) or {}).get("paid")) for r in picked)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since3", required=True)
    parser.add_argument("--until3", required=True)
    parser.add_argument("--out")
    args = parser.parse_args()
    windows = {"same_settings_20s_1121_1456": (at("2026-11-05T11:21:00"), at("2026-11-05T14:56:36")), "ten_seconds_before_market_drop_1457_1608": (at("2026-11-05T14:57:53"), at("2026-11-05T16:08:00")), "ten_seconds_shops_stable": (at(args.since3), at(args.until3))}
    rows = detail.load_records()
    payments = detail.load_payments()
    catalog = json.loads((ROOT / ".market/catalog.json").read_text(encoding="utf-8"))
    state = json.loads((ROOT / ".market/buyer-support-state.json").read_text(encoding="utf-8"))
    attempts = {a["order"] for a in state["attempts"]}
    dated = market.kit_lines()
    report = {}
    for name, (start, end) in windows.items():
        picked = detail.summarize(rows, start, end, payments, catalog)
        tool = measure(ROOT, start, end)
        reached = [r for moment, r in rows if start <= moment < end and r.get("order") in attempts]
        paid_reached = sum(bool((payments.get(r.get("order")) or {}).get("paid")) for r in reached)
        amounts = sorted(((catalog.get(payments[r["order"]].get("item")) or {}).get("list_price") or 0) - payments[r["order"]]["price"] for moment, r in rows if start <= moment < end and (payments.get(r.get("order")) or {}).get("paid"))
        report[name] = {"kst": [start.isoformat(), end.isoformat()], "surplus_without_top3_per_hour": round(sum(amounts[:-3])/((end-start).total_seconds()/3600)), **{k: picked[k] for k in ("hours", "orders", "orders_per_hour", "attempts", "attempts_per_hour", "paid", "paid_per_hour", "paid_per_attempt", "paid_per_attempt_95", "surplus", "surplus_per_hour", "surplus_per_deal_median", "surplus_top3_share", "payment_refused", "shops_attempted", "shops_paid", "paid_by_shop_top")},
                        "reached_a_shop": len(reached), "paid_per_reached": round(paid_reached/len(reached), 4) if reached else None, "paid_per_reached_95": detail.wilson(paid_reached, len(reached)),
                        "transport_error": picked["last_stop"].get("transport_error", 0), "agent_error": picked["last_stop"].get("agent_error", 0), "payment_refused_reasons": tool["payment_refused_reasons"], "kit_log": tool["kit_log"], "tool_agrees": {k: tool[k] == picked[k] for k in ("orders", "attempts", "paid", "payment_refused")}, "last_stop": picked["last_stop"], "errors": [{"at": moment.astimezone(KST).strftime("%H:%M:%S"), "shop": r.get("shop_name"), "stop": detail.last_stop(r), "error": str(r.get("error"))[:90]} for moment, r in rows if start <= moment < end and detail.last_stop(r) in ("agent_error", "transport_error")], "timeout_lines": [raw[:70].decode("utf-8", "replace") for moment, raw in dated if start <= moment < end and b"imeout" in raw], "market": market_in(dated, start, end)}
    start, end = windows["ten_seconds_shops_stable"]
    report["strategy_experiment"] = experiment_reach(rows, start, end, payments)
    report["attempt_state_starts_kst"] = datetime.fromtimestamp(min(a["at"] for a in state["attempts"]), KST).isoformat()
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
