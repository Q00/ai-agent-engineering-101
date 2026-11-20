"""Benchmark official Top 3; use public observations, never infer private policies."""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
from pathlib import Path
import ssl
import statistics
import time
from uuid import uuid4

import httpx

from market_state import read, write
from purchase_monitor import settlement_price

INTERVAL = 1800
BASE = "https://market.ourolabs.ai"
FIELDS = {"shop": ("name", "all", "alive", "deals", "visits", "profit", "revenue", "stars", "reviews", "refunds", "bankruptcies"),
          "buyer": ("name", "all", "alive", "deals", "surplus", "forum", "posts")}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def owner(row):
    value = row.get("owner")
    return str(value.get("login", "") if isinstance(value, dict) else value or "").casefold()


def player_id(row):
    return owner(row) or str(row.get("player") or "").casefold()


def profile(player, board, rank):
    login = player_id(player)
    result = {"login": login, "verified_owner": bool(owner(player)), "rank": rank, "total": player["total"]}
    for role, group in (("shop", "shops"), ("buyer", "buyers")):
        # The same owner may have unused aliases. Follow the scored Players role.
        name = (player.get(role) or {}).get("name")
        matches = [r for r in board.get(group, []) if owner(r) == owner(player) and isinstance(name, str)
                   and str(r.get("name", "")).casefold() == name.casefold()]
        row = matches[0] if len(matches) == 1 else {}
        result[role] = {k: row.get(k) for k in FIELDS[role]}
        result[role]["identity_resolved"] = len(matches) == 1
        if role == "shop":
            result[role]["url"] = str((row.get("card") or {}).get("url", "")).rstrip("/")
    return result


def analyze(board, snapshot, posts, catalog, now, previous=None, login="kcwarni", basis="player_total"):
    players = board.get("players") or []
    if (board.get("round") != 3 or len(players) < 3 or any(not player_id(p) or not finite(p.get("total")) for p in players)
            or len({player_id(p) for p in players}) != len(players)):
        raise ValueError("Complete R3 Players total scores and distinct player identities are required")
    ranked = sorted(players, key=lambda p: (-p["total"], player_id(p)))
    leaders = [profile(p, board, 1+sum(q["total"] > p["total"] for q in ranked)) for p in ranked[:3]]
    mine = next((profile(p, board, 1+sum(q["total"] > p["total"] for q in ranked))
                 for p in ranked if owner(p) == login.casefold()), None)
    if basis not in ("player_total", "shop_profit"):
        raise ValueError("Unknown benchmark basis")
    if basis == "shop_profit":
        shops = sorted((s for s in board.get("shops", []) if finite(s.get("profit"))),
                       key=lambda s: (-s["profit"], str(s.get("name", ""))))
        if len(shops) < 3:
            raise ValueError("Three known shop profits are required")
        leaders = []
        for shop in shops[:3]:
            identity = owner(shop) or str(shop.get("name") or "").casefold()
            player = next((p for p in ranked if player_id(p) == identity),
                          {"owner": shop.get("owner"), "player": identity, "total": None})
            # Profit ranks shops directly, including a non-scored alias if it leads.
            player = {**player, "shop": {"name": shop.get("name")}}
            leaders.append(profile(player, board, 1+sum(s["profit"] > shop["profit"] for s in shops)))
        if mine and finite(mine["shop"].get("profit")):
            mine["rank"] = 1+sum(s["profit"] > mine["shop"]["profit"] for s in shops)
    previous = previous or {}
    endpoints = {key: [r for r in values if 0 <= now-r["at"] <= 86400]
                 for key, values in previous.get("endpoints", {}).items()}
    for leader in leaders:
        key = leader["login"] + "/" + str(leader["shop"].get("name"))
        url = leader["shop"].get("url")
        if url:
            values = [r for r in endpoints.get(key, []) if r["url"] != url]
            endpoints[key] = (values + [{"url": url, "at": now}])[-48:]
    endpoints = {k: v for k, v in endpoints.items() if v}
    url_owners = defaultdict(set)
    for leader in leaders:
        key = leader["login"] + "/" + str(leader["shop"].get("name"))
        for endpoint in endpoints.get(key, []):
            url_owners[endpoint["url"]].add(leader["login"])
    fresh = snapshot.get("round") == 3 and 0 <= now-snapshot.get("observed_at", 0) <= 3600
    rows = {}
    for row in snapshot.get("rows", [])[-10000:] if fresh else []:
        data = row.get("data") or {}
        price = settlement_price(data)
        if (row.get("round") == 3 and row.get("kind") == "settle" and type(row.get("seq")) is int
                and finite(row.get("ts")) and 0 <= now-row["ts"] <= 86400 and price is not None):
            rows[row["seq"]] = row
    groups = defaultdict(list)
    for leader in leaders:
        shop, buyer = leader["shop"], leader["buyer"]
        key = leader["login"] + "/" + str(shop.get("name"))
        urls = {r["url"] for r in endpoints.get(key, []) if len(url_owners[r["url"]]) == 1} if shop["identity_resolved"] else set()
        sales = [r for r in rows.values() if str(r["data"].get("shop", "")).rstrip("/") in urls]
        buys = [r for r in rows.values() if buyer["identity_resolved"] and r["data"].get("buyer") == buyer.get("name")]
        def patterns(entries):
            categories = Counter(catalog.get(r["data"].get("item"), {}).get("category", "unknown") for r in entries)
            skus = Counter(r["data"].get("item") for r in entries if r["data"].get("item"))
            return {"sampled_settlements": len(entries), "categories": [list(item) for item in categories.most_common(5)],
                    "skus": [list(item) for item in skus.most_common(5)],
                    "grade_counts": dict(Counter(r["data"].get("grade", "unknown") for r in entries)),
                    "warranty_hour_counts": dict(Counter(str(r["data"].get("warranty_hours") or 0) for r in entries)),
                    "median_amount": statistics.median(settlement_price(r["data"]) for r in entries) if entries else None}
        leader["sampled_sales"] = patterns(sales)
        leader["sampled_buys"] = patterns(buys)
        forum = [p for p in posts[-2000:] if isinstance(p, dict) and p.get("round") == 3
                 and finite(p.get("ts")) and 0 <= now-p["ts"] <= 86400
                 and (owner(p) == leader["login"] or str(p.get("author", "")).casefold() in
                      {str(shop.get("name", "")).casefold(), str(buyer.get("name", "")).casefold()})]
        leader["forum_observation"] = {"sampled_posts": len(forum),
                 "observed_likes": sum(p["likes"] for p in forum if finite(p.get("likes"))),
                 "observed_dislikes": sum(p["dislikes"] for p in forum if finite(p.get("dislikes"))),
                 "claims_verified": False}
        if leader["login"] == login.casefold() or shop.get("alive") is not True:
            continue
        for row in sales:
            data = row["data"]
            sku, grade = data.get("item"), data.get("grade")
            if (catalog.get(sku, {}).get("kind") == "goods" and grade in ("A", "B")
                    and data.get("claim_honest") is not False
                    and (data.get("warranty_hours") or 0) == 0 and now-row["ts"] <= 21600):
                groups[(sku, grade)].append({"ledger": row["seq"], "price": settlement_price(data),
                     "buyer": data.get("buyer"), "leader": leader["login"], "ts": row["ts"]})
    benchmarks = []
    for (sku, grade), trades in sorted(groups.items()):
        buyers = {r["buyer"] for r in trades if isinstance(r["buyer"], str) and r["buyer"]}
        if len(trades) >= 3 and len(buyers) >= 2:
            benchmarks.append({"sku": sku, "grade": grade, "warranty_hours": 0, "count": len(trades),
                 "median": statistics.median(r["price"] for r in trades), "distinct_buyers": len(buyers),
                 "leaders": sorted({r["leader"] for r in trades}), "ledger_refs": [r["ledger"] for r in trades[-5:]],
                 "latest_sale_at": max(r["ts"] for r in trades)})
    gaps = []
    opportunities = []
    for benchmark in benchmarks:
        sku = benchmark["sku"]
        product = catalog.get(sku, {})
        recent = [r for r in rows.values() if r["data"].get("item") == sku and now-r["ts"] <= 21600]
        buyers = {r["data"].get("buyer") for r in recent if r["data"].get("buyer")}
        value = product.get("list_price")
        if len(buyers) < 3 or not finite(value) or value <= 0:
            continue
        worst_cost = value * .75
        edge = min(value, benchmark["median"]) - worst_cost * 1.01
        if edge > 0:
            opportunities.append({"sku": sku, "grade_observed": benchmark["grade"],
                 "leaders": benchmark["leaders"], "sampled_settles_6h": len(recent), "distinct_buyers_6h": len(buyers),
                 "conservative_edge_estimate": edge, "edge_per_cost": edge/worst_cost,
                 "note": "Weak restock tie-break only; future grade, selling price and sales are not guaranteed."})
    opportunities.sort(key=lambda r: (-r["edge_per_cost"], r["sku"]))
    for role, metrics in (("shop", ("profit", "revenue", "stars")), ("buyer", ("surplus", "forum", "deals"))):
        for field in metrics:
            values = [p[role][field] for p in leaders if p["login"] != login.casefold() and finite(p[role].get(field))]
            own_value = (mine or {}).get(role, {}).get(field)
            median = statistics.median(values) if values else None
            gaps.append({"metric": f"{role}.{field}", "own": own_value, "top3_median": median,
                         "shortfall": max(0, median-own_value) if finite(median) and finite(own_value) else None})
    history = [h for h in previous.get("history", []) if 0 <= now-h["at"] <= 86400
               and h.get("basis", "player_total") == basis][-47:]
    baseline = next((h for h in reversed(history) if 7200 <= now-h["at"] <= 10800), None)
    trends = []
    if baseline:
        for leader in leaders:
            old = next((p for p in baseline["leaders"] if p["login"] == leader["login"]), None)
            if old:
                trends.append({"login": leader["login"], "hours": (now-baseline["at"])/3600,
                               "official_total_change": leader["total"]-old["total"] if finite(leader["total"]) and finite(old.get("total")) else None,
                               "profit_change": leader["shop"]["profit"]-old["profit"] if finite(leader["shop"].get("profit")) and finite(old.get("profit")) else None})
    history.append({"at": now, "basis": basis, "leaders": [{"login": p["login"], "total": p["total"], "profit": p["shop"].get("profit")} for p in leaders]})
    profit_leaders = [{"name": p.get("name"), "login": owner(p), "profit": p["profit"],
                       "revenue": p.get("revenue"), "deals": p.get("deals"), "stars": p.get("stars")}
                      for p in sorted((p for p in board.get("shops", []) if finite(p.get("profit"))),
                                      key=lambda p: (-p["profit"], str(p.get("name", ""))))[:3]]
    return {"round": 3, "status": "ok", "basis": basis, "observed_at": now, "model_calls": 0, "leaders": leaders, "own": mine,
            "profit_leaders": profit_leaders,
            "gap_to_third": max(0, leaders[2]["total"]-mine["total"]) if mine and basis == "player_total" else None,
            "profit_gap_to_first": max(0, leaders[0]["shop"]["profit"]-mine["shop"]["profit"])
                if basis == "shop_profit" and mine and finite(mine["shop"].get("profit")) else None,
            "component_gaps": gaps, "trends_2h": trends, "price_benchmarks": benchmarks[:500],
            "restock_opportunities": opportunities[:20],
            "market_source_at": snapshot.get("observed_at"), "market_sample_fresh": fresh,
            "endpoints": endpoints, "history": history,
            "note": "Explicit benchmark basis: official Players total or shop profit. Partial public trade/forum samples; private policies and causal effects unknown."}


def observe(md, now=None, client=None, board=None, force=False):
    from strategy_controller import acquire, release
    md = Path(md)
    now = time.time() if now is None else now
    handle = acquire(md / "leader-monitor.lock")
    if handle is None:
        return {"status": "busy", "model_calls": 0}
    own_client = client is None and board is None
    try:
        previous = read(md / "leader-intel.json", {})
        settings = read(Path(__file__).resolve().parent / "leader_monitor.json")
        basis = settings["benchmark_basis"]
        if not force and previous.get("basis") == basis and 0 <= now-previous.get("checked_at", -86400) < INTERVAL:
            return previous
        try:
            if board is None:
                client = client or httpx.Client(verify=ssl.create_default_context(), timeout=8)
                response = client.get(BASE + "/api/board", params={"round": 3})
                response.raise_for_status()
                board = response.json()
            buffer = read(md / "forum-buffer.json", {})
            posts = buffer.get("posts", []) if 0 <= now-buffer.get("captured_at", 0) <= 1800 else read(md / "forum.json", [])
            result = analyze(board, read(md / "market-intel.json", {}), posts,
                             read(md / "catalog.json", {}), now, previous, basis=basis)
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as error:
            result = {**previous, "basis": basis, "status": "unavailable", "error": f"{type(error).__name__}: {error}", "model_calls": 0}
        result["checked_at"] = now
        write(md / "leader-intel.json", result)
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        write(md.parent / "logs/leader-monitor" / (stamp + "-" + uuid4().hex[:8] + ".json"), result)
        return result
    finally:
        if own_client and client is not None:
            client.close()
        release(handle)


def enrich(md, candidates, stock, catalog, snapshot, runtime, now):
    """Prefer supported leader comparisons only when market directions agree."""
    from r3_policy import selling_terms
    report = read(md / "leader-intel.json", {})
    if report.get("round") != 3 or report.get("status") != "ok" or not 0 <= now-report.get("observed_at", 0) <= 3600:
        return candidates, {"status": "unavailable"}
    result = list(candidates)
    added = 0
    for unit in stock:
        if unit.get("kind") != "goods":
            continue
        benchmark = next((b for b in report.get("price_benchmarks", []) if b["sku"] == unit["sku"]
                         and b["grade"] == unit["grade"] and b["count"] >= 3 and b["distinct_buyers"] >= 2
                         and b["warranty_hours"] == 0 and 0 <= now-b["latest_sale_at"] <= 21600), None)
        if not benchmark:
            continue
        terms = selling_terms(unit, catalog, now, snapshot.get("skus", {}).get(unit["sku"], {}), runtime)
        if terms["below_cost_allowed"]:
            continue
        def direction(median):
            return -1 if terms["quote"] > median*1.05 else 1 if terms["quote"] < median*.95 else 0
        existing = [c for c in result if c["sku"] == unit["sku"] and c["grade"] == unit["grade"]]
        if any(finite(c.get("median")) and c["median"] > 0 and c.get("count", 0) >= 3
               and direction(c["median"]) * direction(benchmark["median"]) < 0 for c in existing):
            continue
        result = [c for c in result if c not in existing]
        result.append({"sku": unit["sku"], "grade": unit["grade"], "kind": "goods", "floor": terms["floor"],
                       "quote": terms["quote"], "list_price": unit["list_price"], "median": benchmark["median"],
                       "count": benchmark["count"], "benchmark_source": "top3_settlements",
                       "benchmark_basis": report.get("basis", "player_total"),
                       "leader_logins": benchmark["leaders"], "leader_ledger_refs": benchmark["ledger_refs"]})
        added += 1
    return result, {"status": "ok", "observed_at": report["observed_at"],
                   "basis": report.get("basis"),
                   "profit_rank": (report.get("own") or {}).get("rank") if report.get("basis") == "shop_profit" else None,
                   "profit_gap_to_first": report.get("profit_gap_to_first"),
                   "leaders": [{"login": p["login"], "rank": p["rank"], "total": p["total"]} for p in report["leaders"]],
                   "profit_leaders": report.get("profit_leaders", []),
                   "component_gaps": report["component_gaps"], "supported_stock_comparisons": added}


def restock_hints(md, now=None):
    now = time.time() if now is None else now
    report = read(Path(md) / "leader-intel.json", {})
    if report.get("round") != 3 or report.get("status") != "ok" or not 0 <= now-report.get("observed_at", 0) <= 3600:
        return {}
    return {r["sku"]: min(2, len(r["leaders"])) for r in report.get("restock_opportunities", [])[:20]}
