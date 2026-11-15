"""Read-only Shop operations snapshot. No model, network, reservations or writes.

Local timestamps gate cache/event freshness. Market time gates stock expiry and
supply. Missing evidence remains unknown; a quote cancellation is not a price
rejection. Snapshot output deliberately excludes raw messages and certificates.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import time

from market_clock import schedule_now
from shop_diagnostics import build_report, read_json, read_tail, number, response_summary
from sales_performance import valid_sale, economics, own_price, sku_metrics
from shop_policy import policy_at

SKU = re.compile(r"[GF][0-9a-f]{8}\Z")
IDENTITY = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
REASONS = {"price_rejected", "buyer_cancelled", "negotiation_expired", "stock_unavailable",
           "stock_reserved", "model_error", "internal_error", "capacity_busy", "inventory_unavailable"}
STAGES = {"checkout_created", "checkout_cancelled", "settlement_started", "settlement_confirmed",
          "settlement_refused", "settlement_uncertain"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def identity(value):
    return isinstance(value, str) and IDENTITY.fullmatch(value) is not None


def key(row):
    sku, grade, warranty = (row.get(k) for k in ("sku", "grade", "warranty_hours"))
    return ((sku, grade, warranty) if isinstance(sku, str) and SKU.fullmatch(sku)
            and isinstance(grade, str) and grade in {"A", "B", "C"}
            and type(warranty) is int and 0 <= warranty <= 72 else None)


def safe_sales(memory, rnd, now):
    if not isinstance(memory, dict) or memory.get("schema") != 1 or memory.get("round") != rnd:
        return []
    raw = memory.get("sales")
    if not isinstance(raw, list) or len(raw) > 1000:
        return []
    rows = [s for s in raw if valid_sale(s, now) and key(s) and identity(s.get("unit"))]
    by_unit, by_ledger = defaultdict(set), defaultdict(set)
    for s in rows:
        by_unit[s["unit"]].add(s["ledger"])
        by_ledger[s["ledger"]].add(s["unit"])
    out = {}
    conflicts = set()
    for s in rows:
        if len(by_unit[s["unit"]]) != 1 or len(by_ledger[s["ledger"]]) != 1:
            continue
        ident = (s["unit"], s["ledger"])
        if ident in out and out[ident] != s:
            conflicts.add(ident)
        out[ident] = s
    return [s for ident, s in out.items() if ident not in conflicts]


def sale_economics(sale, now, clock):
    s = deepcopy(sale)
    refunds = s.get("refunds", [])
    if (not isinstance(refunds, list) or any(not isinstance(r, dict) or not number(r.get("amount"))
                                           or r["amount"] < 0 for r in refunds)):
        return {"status": "unknown_refunds"}
    # Acquisition is market-clock time; receipt/observed_at are host-clock time.
    if clock.get("status") != "synced" or not number(s.get("supplied_at")):
        return {"status": "unknown_clock_or_cost"}
    offset = now - clock["now"]
    s["supplied_at"] += offset
    if number(s.get("market_settled_at")):
        s["settled_at"] = s["market_settled_at"] + offset
    return economics(s, now)


def historical_economics(sales, now, clock, seconds=86400):
    """Mature risk evidence has its own 24h cohort, separate from 6h funnel.

    A sale made in the last six hours cannot prove mature six-hour warranty
    profitability. Pending sales do not erase older mature evidence; observed
    refunds/ambiguous accounting still prevent a price experiment.
    """
    groups={}
    for sale in sales:
        if not 0<=now-sale["receipt_time"]<=seconds: continue
        g=groups.setdefault(key(sale),{"window_seconds":seconds,"confirmed_sales":0,
            "mature_sales":0,"pending_sales":0,"unknown_economics":0,"net_estimate":0,
            "refunded_sales":0,"observed_refunds":0,"estimated_storage":0,
            "basis":"24h local receipt-time cohort evaluated with current market-clock offset; not current-window profit"})
        g["confirmed_sales"]+=1
        e=sale_economics(sale,now,clock)
        if e.get("observed_refund",0)>0:
            g["refunded_sales"]+=1;g["observed_refunds"]+=e["observed_refund"]
        if e["status"]=="mature_estimate":
            g["mature_sales"]+=1;g["net_estimate"]+=e["net_estimate"]
            g["estimated_storage"]+=e["storage_estimate"]
        elif e["status"]=="pending_warranty":g["pending_sales"]+=1
        else:g["unknown_economics"]+=1
    for g in groups.values():
        g["sufficient_maturity"]=g["mature_sales"]>=3 and not g["unknown_economics"]
        g["refund_rate"]=g["refunded_sales"]/g["confirmed_sales"] if g["confirmed_sales"] else None
    return groups


def quote_timing_by_source(events, rnd, start, end):
    """Attribute latency only to the first observed opening quote itself."""
    allowed = ("model", "fresh_evidence_opening", "recent_unit_quote", "capacity_fallback")
    tasks = defaultdict(list)
    for event in events:
        if (event.get("round")==rnd and identity(event.get("task")) and number(event.get("time"))
                and start<event["time"]<=end):
            tasks[event["task"]].append(event)
    samples = {source:[] for source in (*allowed,"unknown")}
    counts, cancellations = Counter(), Counter()
    ambiguous = 0
    for rows in tasks.values():
        quotes = [r for r in rows if r.get("outcome")=="quote" and r.get("stage")=="opening"]
        if not quotes: continue
        first = min(quotes,key=lambda r:r["time"])
        peers = [r for r in quotes if r["time"]==first["time"]]
        same = all(r.get("decision_source")==first.get("decision_source")
                   and r.get("first_response_seconds")==first.get("first_response_seconds") for r in peers)
        source = first.get("decision_source")
        source = source if same and isinstance(source,str) and source in allowed else "unknown"
        counts[source]+=1
        ambiguous+=not same
        latency = first.get("first_response_seconds")
        if same and number(latency) and latency>=0: samples[source].append(latency)
        cancellations[source]+=any(r.get("reason_code")=="buyer_cancelled"
                                    and r["time"]>=first["time"] for r in rows)
    total = sum(counts.values())
    paths={}
    for source,values in samples.items():
        values.sort()
        paths[source]={"first_quote_tasks":counts[source],"usage_share":counts[source]/total if total else None,
            "latency_samples":len(values),"missing_latency_tasks":counts[source]-len(values),
            "p50_seconds":values[(len(values)-1)//2] if values else None,
            "p95_seconds":values[max(0,math.ceil(len(values)*.95)-1)] if values else None,
            "later_cancelled_tasks":cancellations[source],
            "later_cancellation_rate":cancellations[source]/counts[source] if counts[source] else None}
    return {"first_quote_tasks":total,"ambiguous_first_quotes":ambiguous,"paths":paths,
        "basis":"Unique task's earliest observed opening quote; latency comes only from that quote row.",
        "limits":"Observed same-window cancellations are not necessarily price-caused; source groups differ in stock, buyer and request mix. Missing opening quotes are excluded; this is not a causal effect estimate."}


def window(events, checkout, sales, rnd, start, end, evaluated_at, clock):
    tasks = {}
    for e in events:
        if (e.get("round") != rnd or not identity(e.get("task")) or not number(e.get("time"))
                or not start < e["time"] <= end):
            continue
        t = tasks.setdefault(e["task"], {"first": e, "last": e, "receipt": None, "stages": set()})
        if e["time"] < t["first"]["time"]: t["first"] = e
        if e["time"] >= t["last"]["time"]: t["last"] = e
        if e.get("outcome") == "receipt_issued": t["receipt"] = e
    receipts = Counter(t["receipt"].get("unit") for t in tasks.values() if t["receipt"]
                       and identity(t["receipt"].get("unit")))
    by_receipt = {(s["unit"], s["sku"], s["price"], s["receipt_time"]): s for s in sales}
    for e in checkout:
        t = tasks.get(e.get("task")) if identity(e.get("task")) else None
        r = t["receipt"] if t else None
        stage = e.get("stage")
        if (r and isinstance(stage, str) and stage in STAGES and e.get("unit") == r.get("unit")
                and number(e.get("time")) and r["time"] <= e["time"] <= evaluated_at):
            if stage != "settlement_confirmed" or type(e.get("ledger")) is int and e["ledger"] > 0:
                t["stages"].add(stage)
    groups, joins = {}, []
    for task, t in tasks.items():
        last, receipt = t["last"], t["receipt"]
        k = key(t["first"]) or key(last)
        if k is None:
            continue
        g = groups.setdefault(k, {"tasks": 0, "price_rejected": 0, "cancelled": 0,
            "stockout_requests": 0, "errors": 0, "receipts": 0, "confirmed_sales": 0,
            "unconfirmed_receipts": 0, "settlement_refused": 0, "revenue": 0,
            "mature_sales": 0, "refunded_sales": 0, "net_estimate": 0, "unknown_economics": 0,
            "observed_refunds":0,"estimated_storage":0})
        g["tasks"] += 1
        reason = last.get("reason_code")
        g["price_rejected"] += reason == "price_rejected"
        g["cancelled"] += reason == "buyer_cancelled"
        g["stockout_requests"] += reason == "stock_unavailable"
        g["errors"] += isinstance(reason,str) and reason in {"model_error", "internal_error", "inventory_unavailable", "capacity_busy"}
        g["settlement_refused"] += "settlement_refused" in t["stages"]
        sale = None
        if receipt:
            g["receipts"] += 1
            if (identity(receipt.get("unit")) and receipts[receipt["unit"]] == 1
                    and identity(receipt.get("sku")) and type(receipt.get("price")) is int):
                sale = by_receipt.get((receipt.get("unit"), receipt.get("sku"), receipt.get("price"), receipt["time"]))
            confirmed = sale is not None or "settlement_confirmed" in t["stages"]
            g["confirmed_sales"] += confirmed
            g["unconfirmed_receipts"] += not confirmed
            if confirmed and type(receipt.get("price")) is int: g["revenue"] += receipt["price"]
            econ = sale_economics(sale, evaluated_at, clock) if sale else {"status": "unknown_cost"}
            if econ["status"] == "mature_estimate":
                g["mature_sales"] += 1
                g["net_estimate"] += econ["net_estimate"]
                g["refunded_sales"] += econ["observed_refund"] > 0
                g["observed_refunds"] += econ["observed_refund"]
                g["estimated_storage"] += econ["storage_estimate"]
            elif confirmed:
                g["unknown_economics"] += 1
        joins.append({"task_id": digest(task)[:20], "unit_id": digest(receipt["unit"])[:20] if receipt and identity(receipt.get("unit")) else None,
            "order_id": None, "sku": k[0], "grade": k[1], "warranty_hours": k[2],
            "receipt": bool(receipt), "checkout_stages": sorted(t["stages"]),
            "ledger": sale["ledger"] if sale else None, "refund_observed": bool(sale and sale.get("refunds")),
            "review": "not_correlated", "terminal_reason": reason if isinstance(reason, str) and reason in REASONS else "unknown"})
    duration = (end-start)/3600
    for g in groups.values():
        g.update(technical_errors=g["errors"]+g["settlement_refused"],settled_revenue_per_hour=g["revenue"]/duration,
            refund_adjusted_profit_per_visit=g["net_estimate"]/g["tasks"] if not g["unknown_economics"] else None,
            refund_rate=g["refunded_sales"]/g["mature_sales"] if g["mature_sales"] else None,
            sufficient_maturity=g["mature_sales"] >= 3 and not g["unknown_economics"])
    summary={"observed_tasks": len(tasks), "timing": response_summary(events,rnd,end,start),
        "timing_by_source":quote_timing_by_source(events,rnd,start,end),
        "confirmed_sales": sum(g["confirmed_sales"] for g in groups.values()),
        "revenue": sum(g["revenue"] for g in groups.values()),
        "unconfirmed_receipts": sum(g["unconfirmed_receipts"] for g in groups.values()),
        "groups": [{"sku":k[0],"grade":k[1],"warranty_hours":k[2],**g} for k,g in list(groups.items())[:64]]}
    for metric in ("mature_sales","refunded_sales","net_estimate","unknown_economics","technical_errors","observed_refunds","estimated_storage"):
        summary[metric]=sum(g[metric] for g in groups.values())
    summary["settled_revenue_per_hour"]=summary["revenue"]/duration
    summary["refund_adjusted_profit_per_visit"]=(summary["net_estimate"]/len(tasks)
        if tasks and not summary["unknown_economics"] else None)
    summary["refund_rate"]=summary["refunded_sales"]/summary["mature_sales"] if summary["mature_sales"] else None
    summary["sufficient_maturity"]=summary["mature_sales"]>=3 and not summary["unknown_economics"]
    times=[e["time"] for t in tasks.values() for e in (t["first"],t["last"])]
    summary.update(first_event_at=min(times) if times else None,last_event_at=max(times) if times else None,
        observed_span_seconds=max(times)-min(times) if times else 0,
        requested_window_seconds=end-start,
        coverage="Observed event span only; bounded tails do not prove continuous coverage or an experiment start.")
    return groups,joins[-100:],summary


def collect_snapshot(root, *, now=None, hours=6, rules=None):
    root, now = Path(root), time.time() if now is None else now
    if not number(now) or not number(hours) or not 0 < hours <= 24:
        raise ValueError("Invalid snapshot time or window")
    md, states, sources, blockers = root/".market", {}, {}, []
    for name in ("inventory", "receipt-reservations", "sales-performance", "market-evidence", "restock-control", "stock-lifecycle", "catalog", "hourly-demand"):
        states[name], sources[name] = read_json(md/(name+".json"),now)
    inv, memory = states["inventory"] or {}, states["sales-performance"] or {}
    rnd = inv.get("round") if type(inv.get("round")) is int else (rules or {}).get("round",3)
    clock = schedule_now(md,rnd,local_now=now)
    if clock["status"] != "synced": blockers.append("market_clock_unavailable")
    for name, limit in (("inventory",45),("sales-performance",3600),("receipt-reservations",900)):
        if sources[name]["status"] != "read": blockers.append(name+"_missing")
        elif not -5 <= sources[name].get("age_seconds",-999) <= limit: blockers.append(name+"_stale")
    if memory.get("round") != rnd or inv.get("round") != rnd: blockers.append("round_mismatch")
    if not number(memory.get("updated_at")) or not 0 <= now-memory["updated_at"] <= 3600:
        blockers.append("sales_memory_stale")
    if (not isinstance(rules,dict) or not isinstance(rules.get("version"),str)
            or not number(rules.get("verified_at")) or not 0<=now-rules["verified_at"]<=86400
            or rules.get("round")!=rnd): blockers.append("rules_unverified")
    try: policy = policy_at(root)
    except (ValueError,TypeError,OSError,KeyError): policy = None; blockers.append("policy_invalid")
    events,sources["seller_events"] = read_tail(root/"logs/inventory-seller-events.jsonl",now)
    checkout,sources["checkout_events"] = read_tail(md/"checkout-events.jsonl",now)
    if sources["seller_events"]["status"] != "read": blockers.append("seller_events_missing")
    elif sources["seller_events"].get("age_seconds",1e9)>900: blockers.append("seller_events_stale")
    if sources["seller_events"].get("malformed_rows",0): blockers.append("seller_events_malformed")
    sales = safe_sales(memory,rnd,now)
    groups,joins,current = window(events,checkout,sales,rnd,now-hours*3600,now,now,clock)
    _,_,previous = window(events,checkout,sales,rnd,now-hours*7200,now-hours*3600,now,clock)
    versions=defaultdict(set)
    for e in events:
        version=e.get("ops_policy_version")
        if identity(version) and identity(e.get("task")): versions[version].add(e["task"])
    cohorts={}
    for version,tasks in list(versions.items())[-16:]:
        filtered=[e for e in events if identity(e.get("task")) and e["task"] in tasks]
        _,_,cohort=window(filtered,checkout,sales,rnd,now-hours*3600,now,now,clock)
        cohorts[version]=cohort
    try: report = build_report(root,hours=hours,rnd=rnd,now=now)
    except (ValueError,TypeError,KeyError,AttributeError): report={"status":"invalid_source"}; blockers.append("diagnostics_invalid")
    units = inv.get("units",[])
    if not isinstance(units,list) or len(units)>1000: units=[]; blockers.append("inventory_invalid")
    holds = states["receipt-reservations"]
    available, categories = Counter(),Counter()
    safe_units=[]
    seen_units=set()
    for u in units:
        if not isinstance(u,dict) or not identity(u.get("unit")) or not isinstance(u.get("sku"),str) or not SKU.fullmatch(u["sku"]):
            blockers.append("inventory_invalid"); continue
        if u["unit"] in seen_units:
            blockers.append("duplicate_inventory_unit"); continue
        seen_units.add(u["unit"])
        if isinstance(u.get("category"),str): categories[u["category"][:160]]+=1
        if (not policy or holds is None or u["unit"] in holds or u.get("grade") not in ("A","B","C")
                or u.get("kind") not in ("goods","food")): continue
        if u["kind"]=="food" and (clock["status"]!="synced" or not number(u.get("expires_at")) or u["expires_at"]<=clock["now"]): continue
        warranty=policy["goods_warranty_hours"][u["grade"]] if u["kind"]=="goods" else 0
        available[(u["sku"],u["grade"],warranty)]+=1
        safe_units.append(u)
    candidates=[]
    clean_memory={**memory,"sales":sales}
    history=historical_economics(sales,now,clock)
    for k,g in sorted(groups.items()):
        historical=history.get(k,{"window_seconds":86400,"sufficient_maturity":False,
                                  "mature_sales":0,"net_estimate":0,"refunded_sales":0})
        reasons=[]
        if g["tasks"]<10: reasons.append("insufficient_tasks")
        if g["price_rejected"]<3: reasons.append("insufficient_explicit_price_rejections")
        if available[k]<2: reasons.append("insufficient_matching_stock")
        if g["errors"] or g["settlement_refused"]: reasons.append("technical_or_settlement_error")
        if not historical["sufficient_maturity"]: reasons.append("insufficient_mature_economics")
        if historical["net_estimate"]<=0 or historical["refunded_sales"]: reasons.append("profit_or_refund_risk")
        if k[1]=="C": reasons.append("grade_c_not_automatic")
        product=next((u for u in safe_units if u["sku"]==k[0] and u["grade"]==k[1]),None)
        ref=own_price(clean_memory,product,k[2],rnd,now,market_now=clock["now"] if clock["status"]=="synced" else None) if product else None
        if not ref: reasons.append("insufficient_same_quality_settlements")
        candidates.append({"sku":k[0],"grade":k[1],"warranty_hours":k[2],"eligible":not reasons and not blockers,
            "reasons":reasons,"available_units":available[k],"metrics":g,"historical_economics":historical,"reference":ref,
            "proposed_change":{"opening_discount_percent":1}})
    # demand.json is a list; read_json deliberately accepts only object caches.
    try:
        with (md/"demand.json").open("rb") as stream: raw=stream.read(2_000_001)
        if len(raw)>2_000_000: raise ValueError("oversized")
        demand=json.loads(raw)
        if not isinstance(demand,list) or len(demand)>5000: raise ValueError("invalid")
        states["demand"]=demand
    except (OSError,ValueError): states["demand"]=None
    stockouts=defaultdict(set)
    for e in events:
        sku=e.get("sku")
        if (e.get("round")==rnd and e.get("reason_code")=="stock_unavailable"
                and identity(e.get("task")) and isinstance(sku,str) and SKU.fullmatch(sku)
                and number(e.get("time")) and now-hours*3600<e["time"]<=now):
            stockouts[sku].add(e["task"])
    inventory_analysis = stock_analysis(inv,states,policy,sales,groups,available,clock,now,
                                        stockouts={sku:len(tasks) for sku,tasks in stockouts.items()})
    inventory_analysis["category_units"]=dict(categories)
    inventory_analysis["largest_category_share"]=max(categories.values())/sum(categories.values()) if categories else None
    result={"schema":1,"generated_at":now,"round":rnd,"rules_version":rules.get("version") if isinstance(rules,dict) else None,
        "market_now":clock["now"] if clock["status"]=="synced" else None,"clock":clock,
        "application_ready":not blockers,"blockers":sorted(set(blockers)),"hours":hours,
        "diagnostics":report,"current":current,"previous":previous,"policy_cohorts":cohorts,"task_sample":joins,
        "timing_by_source":current["timing_by_source"],
        "candidates":candidates[:64],"inventory_analysis":inventory_analysis,"sources":sources,
        "limits":["Bounded local samples; observed tasks are not all board visits.",
            "Receipt without settlement evidence is unconfirmed, not failed; cancellation is not a price diagnosis.",
            "In-process reservations and buyer use-time conditions are not available to this snapshot.",
            "Order IDs and reviews are not guessed. Refund and storage estimates may be incomplete.",
            "Previous/current periods share the current observation cutoff for late confirmations and refunds."]}
    result["snapshot_id"]=digest(result)
    return result


def evaluation_tail(path, now):
    """Larger but bounded read for fixed cohorts after warranties mature."""
    rows, meta = read_tail(path, now)
    if not meta.get("truncated"):
        return rows, meta
    limit_bytes, limit_rows = 16_000_000, 32000
    try:
        with path.open("rb") as stream:
            start = max(0, stream.seek(0, 2)-limit_bytes)
            stream.seek(start)
            raw = stream.read(limit_bytes)
        lines = raw.splitlines()
        if start: lines = lines[1:]
        partial = bool(raw and not raw.endswith(b"\n"))
        if partial: lines = lines[:-1]
        meta = {"status":"read", "limit_bytes":limit_bytes, "limit_rows":limit_rows,
            "truncated":bool(start or len(lines)>limit_rows), "malformed_rows":0,
            "partial_last_line":partial, "age_seconds":round(now-path.stat().st_mtime,1)}
        rows = []
        for line in lines[-limit_rows:]:
            try:
                row = json.loads(line)
                if not isinstance(row,dict): raise ValueError("expected object")
                rows.append(row)
            except (ValueError,UnicodeError): meta["malformed_rows"] += 1
        meta["rows_read"] = len(rows)
        return rows, meta
    except OSError:
        return [], {**meta,"status":"unreadable"}


def evaluate_windows(root, *, version, start, end, as_of, rules, baseline_hours=6):
    """Re-evaluate fixed task-start cohorts with a shared late-outcome cutoff.

    All retained events for selected tasks are joined, including receipts and
    checkout outcomes after the intervention ended. Rates use the fixed cohort
    exposure, never the later warranty observation period. Comparable denotes
    adequate bounded source coverage, not causal identification or profitability.
    """
    if (not identity(version) or not all(number(v) for v in (start,end,as_of,baseline_hours))
            or not 0 < end-start <= 86400 or not 0 <= as_of-end <= 43200
            or not 0 < baseline_hours <= 24 or end-start+baseline_hours*3600 > 86400):
        raise ValueError("invalid fixed evaluation window")
    root = Path(root)
    baseline_start = start-baseline_hours*3600
    rnd = rules.get("round") if isinstance(rules,dict) else None
    if type(rnd) is not int or not isinstance(rules.get("version"),str):
        raise ValueError("verified round and rules required")
    clock = schedule_now(root/".market",rnd,local_now=as_of)
    events, event_meta = evaluation_tail(root/"logs/inventory-seller-events.jsonl",as_of)
    checkout, checkout_meta = evaluation_tail(root/".market/checkout-events.jsonl",as_of)
    memory, memory_meta = read_json(root/".market/sales-performance.json",as_of)
    sales = safe_sales(memory,rnd,as_of)
    reasons = []
    if clock.get("status") != "synced": reasons.append("market_clock_unavailable")
    if not number(rules.get("verified_at")) or not 0<=as_of-rules["verified_at"]<=86400:
        reasons.append("rules_unverified")
    for name,meta in (("seller_events",event_meta),("checkout_events",checkout_meta)):
        if meta.get("status")!="read": reasons.append(name+"_unavailable")
        if meta.get("truncated"): reasons.append(name+"_truncated")
        if meta.get("malformed_rows") or meta.get("partial_last_line"): reasons.append(name+"_incomplete")
    if (memory_meta.get("status")!="read" or not isinstance(memory,dict)
            or memory.get("round")!=rnd or not number(memory.get("updated_at"))
            or not 0<=as_of-memory["updated_at"]<=3600): reasons.append("sales_memory_unavailable_or_stale")
    valid = [e for e in events if e.get("round")==rnd and identity(e.get("task"))
             and number(e.get("time")) and e["time"]<=as_of]
    times = [e["time"] for e in valid]
    if not times or min(times)>baseline_start+60: reasons.append("baseline_start_not_covered")
    if not times or max(times)<end-60: reasons.append("experiment_end_not_covered")
    by_task = defaultdict(list)
    for event in valid: by_task[event["task"]].append(event)
    cohorts = {"baseline":[], "experiment":[]}
    missing_open = Counter()
    conflicts = 0
    for task_events in by_task.values():
        task_events.sort(key=lambda e:e["time"])
        opened = next((e for e in task_events if e.get("outcome")=="opened"),None)
        first = opened or task_events[0]
        name = "baseline" if baseline_start<=first["time"]<start else "experiment" if start<=first["time"]<=end else None
        if name is None: continue
        if opened is None:
            missing_open[name]+=1
            continue
        tags = {e.get("ops_policy_version") for e in task_events if e.get("ops_policy_version") is not None
                and isinstance(e.get("ops_policy_version"),str)}
        if name=="experiment":
            if version not in tags: continue
            if tags!={version}:
                conflicts+=1
                continue
        cohorts[name].extend(task_events)
    if conflicts: reasons.append("conflicting_policy_version_tags")
    result = {}
    for name,lo,hi in (("baseline",baseline_start,start),("experiment",start,end)):
        _,_,summary = window(cohorts[name],checkout,sales,rnd,baseline_start-1,as_of,as_of,clock)
        duration=(hi-lo)/3600
        summary.update(cohort_start=lo,cohort_end=hi,evaluated_at=as_of,
            requested_window_seconds=hi-lo,exposure_seconds=hi-lo,
            settled_revenue_per_hour=summary["revenue"]/duration,
            missing_open_tasks_excluded=missing_open[name],
            coverage="Fixed opened-task cohort; late outcomes included at shared as_of; bounded sources do not prove causal comparability.")
        for group in summary["groups"]: group["settled_revenue_per_hour"]=group["revenue"]/duration
        if not summary["observed_tasks"]: reasons.append(name+"_no_observed_opened_tasks")
        if missing_open[name]: reasons.append(name+"_missing_open_events")
        if summary["unconfirmed_receipts"]: reasons.append(name+"_unconfirmed_settlement_coverage")
        result[name] = summary
    result.update(comparable=not reasons,coverage={"reasons":sorted(set(reasons)),
        "sources":{"seller_events":event_meta,"checkout_events":checkout_meta,"sales_memory":memory_meta},
        "first_event_at":min(times) if times else None,"last_event_at":max(times) if times else None,
        "version":version,"as_of":as_of,"round":rnd,"rules_version":rules["version"],
        "limits":["No open event means unknown cohort membership, not a failed visit.",
            "Unconfirmed receipt remains unknown, never a payment failure.",
            "Both periods use current confirmation/refund evidence; bounded cache may omit old settlements.",
            "Source span does not prove continuous logging; demand, supply and buyer mix remain confounders."]})
    return result


def stock_analysis(inv,states,policy,sales,groups,available,clock,now,*,stockouts=None):
    if not policy: return {"status":"policy_unavailable","targets":[]}
    cfg=policy["restock"]
    schedule=inv.get("restock",{})
    until=None
    if (clock["status"]=="synced" and isinstance(schedule,dict) and number(schedule.get("opens_at"))
            and number(schedule.get("closes_at")) and schedule["closes_at"]>schedule["opens_at"]
            and type(schedule.get("open")) is bool and schedule["closes_at"]>clock["now"]):
        until=max(0,schedule["opens_at"]-clock["now"])
    recent=Counter(s["sku"] for s in sales if 0<=now-s["receipt_time"]<=3600)
    try: economic_metrics=sku_metrics({"sales":sales},now)
    except (ValueError,TypeError,KeyError): economic_metrics={}
    missing=Counter(stockouts or {})
    held=Counter()
    for k,n in available.items(): held[k[0]]+=n
    targets=[]
    catalog=states["catalog"] or {}
    for sku in sorted(set(recent)|set(missing)|set(held))[:64]:
        product=catalog.get(sku,{})
        cap=cfg["max_food_units_per_sku"] if product.get("kind")=="food" else cfg["max_units_per_sku"]
        from adaptive_stock import depth_cap
        try: cap=min(cap,depth_cap(economic_metrics.get(sku,{}),cfg,product.get("kind","goods")))
        except (ValueError,TypeError,KeyError): cap=min(cap,1)
        cover=None if until is None else until+1200
        target=min(cap,math.ceil(recent[sku]*cover/3600)+int(recent[sku]>0)) if cover is not None else None
        targets.append({"sku":sku,"confirmed_sales_last_hour":recent[sku],"stockout_requests_observed":missing[sku],
            "stockout_qualification":"SKU known; buyer conditions unknown, excluded from automatic demand forecast",
            "available_units":held[sku],"cover_seconds":cover,"target_units":target,
            "needed_units":max(0,target-held[sku]) if target is not None else None,"existing_cap":cap})
    liabilities=sum(s["price"] for s in sales if sale_economics(s,now,clock).get("status")=="pending_warranty")
    result={"status":"advisory","targets":targets,"max_stock_units":cfg["max_stock_units"],
        "held_units":len(inv.get("units",[])),"next_window_seconds":until,
        "pending_warranty_max_price_exposure":liabilities,
        "limitations":"Targets are forecasts, not purchase instructions; all existing profitability/cash/supply gates still apply."}
    try:
        from stock_lifecycle import metrics as lifecycle_metrics
        state=states["stock-lifecycle"]
        if clock["status"]!="synced" or not isinstance(state,dict) or state.get("round")!=inv.get("round"):
            raise ValueError("no usable lifecycle clock")
        result["lifecycle"]=dict(list(lifecycle_metrics(state,clock["now"]).items())[:64])
    except (ValueError,TypeError,KeyError,AttributeError):result["lifecycle"]={"status":"unavailable"}
    # The previous planner's rich blockers remain useful even when data cannot
    # safely support a new pure shadow calculation. Never run run_once/update.
    try:
        from restock import plan
        control=states["restock-control"] or {}
        if (clock["status"]!="synced" or not states["catalog"] or not states.get("demand")
                or control.get("round")!=inv.get("round")):
            raise ValueError("shadow inputs unavailable")
        horizon=deepcopy(policy["horizon"])
        # Planner uses local history timestamps: convert only the market deadline.
        deadline=datetime.fromisoformat(horizon["ends_at"]).timestamp()+now-clock["now"]
        horizon["ends_at"]=datetime.fromtimestamp(deadline,timezone.utc).isoformat()
        decision,evidence=plan(deepcopy(inv),catalog,states["demand"],{"sales":sales},control.get("attempts",[]),cfg,now,
            states["market-evidence"],lifecycle=states["stock-lifecycle"],horizon=horizon,
            focus_control=control.get("focused_supply"),adaptive_control=control.get("adaptive_supply"),research=states["hourly-demand"])
        result["shadow_plan"]={"buy":decision["buy"],"allocation":evidence["allocation"],
            "adaptive_exclusions":evidence["adaptive_goods"].get("exclusion_counts"),"exposure":evidence["exposure"]}
    except (ValueError,TypeError,KeyError,AttributeError,OSError): result["shadow_plan"]={"status":"inputs_unavailable"}
    return result
