"""Run the unmodified course kit with structured restock outcome tracking."""
import ast
from collections import Counter, defaultdict
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import time


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if path.exists() else []


def reservation_id(row, index):
    return row.get("reservation_id", f"legacy-{index}-{row['sku']}")


def stock_budget_status(state_dir, cap):
    state_dir = Path(state_dir)
    rows = read_rows(state_dir / "restock-reservations.jsonl")
    outcomes = {}
    for event in read_rows(state_dir / "restock-outcomes.jsonl"):
        rid = event["reservation_id"]
        if rid in outcomes and outcomes[rid] != event:
            raise ValueError("Conflicting stock outcomes; manual review required")
        outcomes[rid] = event
    charged, pending_skus, ids = 0, set(), set()
    for index, row in enumerate(rows):
        rid = reservation_id(row, index)
        if rid in ids:
            raise ValueError("Duplicate stock reservation ID")
        ids.add(rid)
        bound = row["reserved_cost"]
        if type(bound) is not int or bound < 0:
            raise ValueError("Invalid reserved cost")
        event = outcomes.get(rid)
        if event and event["status"] == "purchased":
            cost = event["actual_cost"]
            if type(cost) is not int or not 0 <= cost <= bound:
                raise ValueError("Supply cost exceeds reservation; stop buying")
            charged += cost
        elif event and event["status"] in {"refused", "not_attempted"}:
            pass
        else:
            charged += bound
            pending_skus.add(row["sku"])
    return {"charged": charged, "remaining": max(0, cap - charged),
            "pending_skus": pending_skus, "outcomes": outcomes,
            "next_open": max((e.get("next_open", 0) for e in outcomes.values()), default=0)}


def append_outcome(state_dir, logs_dir, event):
    for path in (Path(state_dir) / "restock-outcomes.jsonl",
                 Path(logs_dir) / "restock-outcomes.jsonl"):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def reconcile_legacy(state_dir, logs_dir, cap, apply=False):
    """Match uniquely identifiable old requests; ambiguous outcomes stay reserved."""
    state_dir, logs_dir = Path(state_dir), Path(logs_dir)
    rows = read_rows(state_dir / "restock-reservations.jsonl")
    before = stock_budget_status(state_dir, cap)
    catalog = json.loads((state_dir / "catalog.json").read_text(encoding="utf-8"))
    counts = Counter(row["sku"] for row in rows)
    names = defaultdict(list)
    for sku in counts:
        if sku in catalog:
            names[catalog[sku]["name"]].append(sku)
    successes, failures = defaultdict(list), defaultdict(list)
    for path in sorted(logs_dir.glob("market-kit.log*")):
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            success = re.match(r"\d{2}:\d{2}:\d{2} \[supply\] (\d+) x (.*?) for (\d+), balance ", line)
            if success and success[1] == "1" and len(names[success[2]]) == 1:
                successes[names[success[2]][0]].append((int(success[3]), str(path), number))
            failed = re.match(r"\d{2}:\d{2}:\d{2} \[supply\] (\{.*?\}) refused: (\d+)", line)
            if failed and int(failed[2]) in {400, 402, 429}:
                buy = ast.literal_eval(failed[1])
                failures[buy["sku"]].append((str(path), number))
    events = []
    for index, row in enumerate(rows):
        rid, sku = reservation_id(row, index), row["sku"]
        if "reservation_id" in row or rid in before["outcomes"] or counts[sku] != 1:
            continue
        event = {"time": time.time(), "reservation_id": rid, "sku": sku,
                 "source": "legacy_log_reconciliation"}
        if len(successes[sku]) == 1 and not failures[sku]:
            cost, source, line = successes[sku][0]
            if not 0 <= cost <= row["reserved_cost"]:
                continue
            event.update(status="purchased", actual_cost=cost, evidence=source, line=line)
        elif failures[sku] and not successes[sku]:
            source, line = failures[sku][0]
            event.update(status="refused", evidence=source, line=line)
        else:
            continue
        events.append(event)
    if apply:
        for event in events:
            append_outcome(state_dir, logs_dir, event)
    return {"applied": apply, "before_charged": before["charged"],
            "confirmed_purchases": sum(e["status"] == "purchased" for e in events),
            "confirmed_refusals": sum(e["status"] == "refused" for e in events),
            "after": {k: v for k, v in stock_budget_status(state_dir, cap).items()
                      if k in {"charged", "remaining"}} if apply else None}


def tracked_restock(kit, agent, directory, market, state_dir, inventory, demand):
    """Same supply API as the kit, with exact request/result IDs and window waits."""
    out = kit.last_json(kit.run_buyer(kit.fill(agent["restock"]["cmd"], state=state_dir), directory)) or {}
    buys = out.get("buy") or []
    if not isinstance(buys, list):
        return
    records = {reservation_id(row, i): row for i, row in enumerate(
        read_rows(Path(state_dir) / "restock-reservations.jsonl"))}
    cap = json.loads((Path(directory) / "round3_rules.json").read_text(encoding="utf-8"))["stock_budget"]
    budget = stock_budget_status(state_dir, cap)
    handled = set(budget["outcomes"])
    if budget["charged"] > cap:
        kit.log("[supply] stopped: existing charges exceed configured budget")
        return
    for index, buy in enumerate(buys[:10]):
        rid = buy.get("reservation_id")
        record = records.get(rid)
        if not record or record["sku"] != buy.get("sku") \
                or type(buy.get("qty")) is not int or buy["qty"] != 1:
            kit.log("[supply] skipped untracked or invalid request")
            continue
        if rid in handled:
            continue
        event = {"time": time.time(), "reservation_id": rid, "sku": buy["sku"],
                 "source": "supply_api_response"}
        stop = False
        try:
            for attempt in range(3):
                response = market.call("POST", "/api/supply", "shop",
                                       json={"sku": buy["sku"], "qty": 1})
                body = response.json()
                wait = float(body.get("wait", 0)) if response.status_code == 429 else 0
                if response.status_code == 429 and 0 < wait <= 15 and attempt < 2:
                    kit.time.sleep(wait + 0.05)
                    continue
                break
            if response.status_code == 200:
                cost = body["total"]
                if type(cost) is not int or cost < 0:
                    raise ValueError("Supply returned an invalid total")
                event.update(status="purchased", actual_cost=cost)
            elif response.status_code in {400, 402, 429}:
                event.update(status="refused", http_status=response.status_code,
                             reason=str(body.get("error", ""))[:300])
                if response.status_code == 429:
                    event["next_open"] = max(time.time() + max(wait, 1), body.get("opens_at", 0))
                stop = response.status_code in {402, 429}
            else:
                event.update(status="uncertain", http_status=response.status_code)
                stop = True
        except Exception as error:
            event.update(status="uncertain", reason=f"{type(error).__name__}: {error}")
            stop = True
        append_outcome(state_dir, Path(directory) / "logs", event)
        handled.add(rid)
        if event["status"] == "purchased":
            kit.refresh_inventory(market, state_dir)
            kit.log(f"[supply] 1 x {kit.CATALOG.get(buy['sku'], {}).get('name', buy['sku'])} "
                    f"for {event['actual_cost']}, balance {body.get('balance')}")
        else:
            kit.log(f"[supply] {buy['sku']} {event['status']}: {event.get('reason', '')}")
        if stop:
            for leftover in buys[index + 1:10]:
                lid = leftover.get("reservation_id")
                if lid in records and lid not in handled:
                    append_outcome(state_dir, Path(directory) / "logs", {
                        "time": time.time(), "reservation_id": lid, "sku": leftover["sku"],
                        "status": "not_attempted", "source": "remaining_batch_deferred"})
                    handled.add(lid)
            break


def main():
    source = Path(__file__).resolve().parents[3] / "scripts" / "market_kit.py"
    spec = importlib.util.spec_from_file_location("course_market_kit", source)
    kit = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = kit
    spec.loader.exec_module(kit)
    if kit.KIT_VERSION != "4":
        raise RuntimeError("Restock adapter expects course kit v4")
    os.environ["RESTOCK_OUTCOME_TRACKING"] = "v2"
    kit.restock = lambda *args: tracked_restock(kit, *args)
    kit.main()


if __name__ == "__main__":
    main()
