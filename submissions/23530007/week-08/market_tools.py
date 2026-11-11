"""Read-only market tools for the strategists, served over MCP stdio (11-11; user: build the tools and use them).

The strategists ran with no tools: everything had to be pushed into a 60-100k-char prompt, and a follow-up question
went to the curator and came back 1-2 h later (4 of 10 answers 'not measurable'). With these tools a strategist can
check a cause inside the run. All tools are read-only (local files + public market GETs) and cost no API credit;
each answer is capped at MAX_CHARS.

Run by `claude -p --mcp-config <json> --strict-mcp-config --allowedTools mcp__market`; see MCP_CONFIG below.
A minimal JSON-RPC 2.0 / MCP stdio server, no SDK (the venv has none).

    python market_tools.py --selftest      # call every tool once and print the sizes
"""
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
MAX_CHARS = 6000
PROTOCOL = "2025-06-18"


def _read(p, default=None):
    try:
        return json.loads((HERE / p).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _score(t):
    if t.get("broken"):
        return 0.0 if (t.get("warranty") or 0) >= 6 else -1.0
    return 1 - t["price"] / t["list"]


def _mean(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def _trades(hours):
    now = time.time()
    return [t for t in (_read("buyer_memory.json", {}) or {}).get("trades", []) if now - t.get("t", 0) < hours * 3600]


# --- tools -------------------------------------------------------------------------------------------------

def our_trades(shop=None, kind=None, category=None, outcome=None, hours=6, limit=25):
    """Our buyer's visit records over the last `hours` (<= 48), filtered; summary + the newest rows."""
    hours = min(float(hours or 6), 48)
    rows = _trades(hours)
    if shop:
        rows = [t for t in rows if t.get("shop") == shop]
    if kind:
        rows = [t for t in rows if t.get("kind") == kind]
    if category:
        rows = [t for t in rows if category in str(t.get("category") or "")]
    if outcome:
        rows = [t for t in rows if str(t.get("outcome", "")).startswith(outcome)]
    deals = [t for t in rows if t.get("outcome") == "deal" and t.get("list") and t.get("price")]
    outs = {}
    for t in rows:
        k = str(t.get("outcome", "")).split(" (")[0].split(":")[0]
        outs[k] = outs.get(k, 0) + 1
    keep = ("shop", "kind", "category", "outcome", "price", "list", "cap", "asks", "warranty", "graded", "broken", "expected")
    sample = [{k: t.get(k) for k in keep if t.get(k) not in (None, [], "")} for t in rows[-int(limit or 25):]]
    return {"visits": len(rows), "outcomes": outs, "deals": len(deals), "v_per_deal": _mean([_score(t) for t in deals]),
            "breaks": sum(1 for t in deals if t.get("broken")), "newest": sample}


def shop_market(shop, kind=None):
    """The whole market's settles at `shop` over the last ~6 h (ledger cache): n, saved per deal, price quantiles
    (x = price/list), warranty and break shares, per kind and the top categories; plus OUR realized v there (24 h)."""
    st = _read(".market/shop_value.json", {}) or {}
    stats = st.get("stats") or {}
    out = {k: v for k, v in stats.items() if k.startswith(f"{shop}|") and (not kind or f"|{kind}" in k)}
    cats = sorted(((k, v) for k, v in out.items() if k.count("|") == 2), key=lambda kv: -kv[1].get("n", 0))[:8]
    base = {k: v for k, v in out.items() if k.count("|") == 1}
    ours = [t for t in _trades(24) if t.get("shop") == shop and t.get("outcome") == "deal" and t.get("list") and t.get("price")
            and (not kind or t.get("kind") == kind)]
    return {"market": base, "by_category": dict(cats), "ours_24h": {"n": len(ours), "v": _mean([_score(t) for t in ours])}}


def food_losses(hours=6):
    """Food orders that ended without a deal: count by final outcome; failed visits by shop with the shop's lowest
    ask and our cap (x = price/list) and the v a deal at that ask would have scored; too-short by shop."""
    rows = [t for t in _trades(min(float(hours or 6), 48)) if t.get("kind") == "food"]
    orders = {}
    for t in rows:
        orders.setdefault(t.get("order"), []).append(t)
    end = {}
    for xs in orders.values():
        k = "deal" if any(t.get("outcome") == "deal" for t in xs) else str(xs[-1].get("outcome", "")).split(" (")[0].split(":")[0]
        end[k] = end.get(k, 0) + 1
    by = {}
    for t in rows:
        o = str(t.get("outcome", "")).split(" (")[0]
        if t.get("shop") and o in ("rejected", "too dear", "over budget", "walked away"):
            e = by.setdefault(t["shop"], {"failed": 0, "asks": [], "caps": []})
            e["failed"] += 1
            if t.get("asks") and t.get("list"):
                e["asks"].append(min(t["asks"]) / t["list"])
            if t.get("cap") and t.get("list"):
                e["caps"].append(t["cap"] / t["list"])
    shops = {s: {"failed": e["failed"], "lowest_ask_x": round(statistics.median(e["asks"]), 3) if e["asks"] else None,
                 "our_cap_x": round(statistics.median(e["caps"]), 3) if e["caps"] else None,
                 "v_at_ask": round(1 - statistics.median(e["asks"]), 3) if e["asks"] else None}
             for s, e in sorted(by.items(), key=lambda kv: -kv[1]["failed"])[:12]}
    short = {}
    for t in rows:
        if str(t.get("outcome", "")).startswith("too short"):
            short[t.get("shop")] = short.get(t.get("shop"), 0) + 1
    return {"food_orders": len(orders), "ended": end, "failed_visits_by_shop": shops,
            "too_short_by_shop": dict(sorted(short.items(), key=lambda kv: -kv[1])[:8]),
            "note": "a deal helps the final score only if v > m* (see score); v_at_ask under m* means the refusal was right"}


def realized_by_shop(kind=None, hours=24):
    """Our realized v per shop over `hours` (deals n >= 3), sorted by volume, with the share of our deals."""
    deals = [t for t in _trades(min(float(hours or 24), 48)) if t.get("outcome") == "deal" and t.get("list") and t.get("price")
             and (not kind or t.get("kind") == kind)]
    by = {}
    for t in deals:
        by.setdefault(t.get("shop"), []).append(_score(t))
    rows = sorted(((s, len(v), _mean(v)) for s, v in by.items() if len(v) >= 3), key=lambda r: -r[1])
    return {"deals": len(deals), "v": _mean([_score(t) for t in deals]),
            "by_shop": [{"shop": s, "n": n, "share": round(n / len(deals), 3), "v": v} for s, n, v in rows[:20]]}


def class_ladder():
    """Students' saved ladder around us with the deals at v .36/.42 needed to pass each (from buyer_strategist)."""
    import buyer_strategist as bs
    return bs.class_ladder(None)


def buyer_profile(name):
    """Another buyer's last-6-h profile from the ledger cache: shops, kinds, grades, prices, saved per deal."""
    import market_picture as mp
    return mp.profile_buyer(name)


def shop_profile(name):
    """A shop's last-6-h profile: what it sells, prices, buyers, stock."""
    import market_picture as mp
    p = mp.profile_shop(name)
    return {k: (v[:8] if isinstance(v, list) else v) for k, v in p.items()} if isinstance(p, dict) else p


def score():
    """The scorekeeper (buyer_score.json): m_now, m*, v by kind, calibration, breaks, skips, arms per order."""
    sc = _read(".market/buyer_score.json", {}) or {}
    return {k: v for k, v in sc.items() if k not in ("hist", "ours")}


TOOLS = {
    "our_trades": (our_trades, "Our buyer's visit records (last hours <= 48), filtered by shop/kind/category/outcome prefix: outcome counts, deals, v per deal (rules scoring: refunded break 0, unrefunded -1), breaks, newest rows.",
                   {"shop": "string", "kind": "string", "category": "string", "outcome": "string", "hours": "number", "limit": "integer"}, []),
    "shop_market": (shop_market, "The whole market's settles at a shop (~6 h): n, saved per deal, p15/p25/p50 price quantiles (x = price/list), warranty and break shares, by kind and category; plus our realized v there over 24 h.",
                    {"shop": "string", "kind": "string"}, ["shop"]),
    "food_losses": (food_losses, "Food orders that ended without a deal (last hours): outcomes, failed visits by shop with the shop's lowest ask vs our cap and the v a deal at that ask would score, too-short by shop.",
                    {"hours": "number"}, []),
    "realized_by_shop": (realized_by_shop, "Our realized v per shop (last hours, default 24), by kind, with each shop's share of our deals.",
                         {"kind": "string", "hours": "number"}, []),
    "class_ladder": (class_ladder, "Students' saved ladder around us and the deals needed to pass each at v .36/.42.", {}, []),
    "buyer_profile": (buyer_profile, "Another buyer's last-6-h profile (shops, kinds, grades, prices, saved per deal).", {"name": "string"}, ["name"]),
    "shop_profile": (shop_profile, "A shop's last-6-h profile (what it sells, prices, buyers, stock).", {"name": "string"}, ["name"]),
    "score": (score, "The scorekeeper: m_now, m* (projected final mean), v by kind 6/24 h, calibration, breaks, skips, per-order arm scores.", {}, []),
}


def _schema(props, req):
    return {"type": "object", "properties": {k: {"type": t} for k, t in props.items()}, "required": req}


def call(name, args):
    fn = TOOLS[name][0]
    try:
        res = fn(**(args or {}))
        text = json.dumps(res, ensure_ascii=False, default=str)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"error: {type(e).__name__}: {e}"}], "isError": True}
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + " ...(truncated)"
    try:
        with open(HERE / "logs" / "market_tools.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {name} {json.dumps(args, ensure_ascii=False)} -> {len(text)} chars\n")
    except OSError:
        pass
    return {"content": [{"type": "text", "text": text}]}


def serve():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        mid, method = msg.get("id"), msg.get("method")
        if mid is None:                               # notifications (e.g. notifications/initialized)
            continue
        if method == "initialize":
            res = {"protocolVersion": (msg.get("params") or {}).get("protocolVersion") or PROTOCOL,
                   "capabilities": {"tools": {}}, "serverInfo": {"name": "market", "version": "1.0"}}
        elif method == "tools/list":
            res = {"tools": [{"name": n, "description": d, "inputSchema": _schema(p, r)} for n, (_, d, p, r) in TOOLS.items()]}
        elif method == "tools/call":
            p = msg.get("params") or {}
            if p.get("name") not in TOOLS:
                sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": "unknown tool"}}) + "\n")
                sys.stdout.flush()
                continue
            res = call(p["name"], p.get("arguments") or {})
        elif method == "ping":
            res = {}
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "method not found"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": res}, ensure_ascii=False) + "\n")
        sys.stdout.flush()


MCP_CONFIG = HERE / ".market" / "mcp_market.json"


def write_config():
    MCP_CONFIG.parent.mkdir(exist_ok=True)
    MCP_CONFIG.write_text(json.dumps({"mcpServers": {"market": {"command": str(HERE / ".venv" / "bin" / "python"),
                                                                   "args": [str(HERE / "market_tools.py")]}}}))
    return MCP_CONFIG


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        for n, args in (("our_trades", {"kind": "food", "hours": 3}), ("shop_market", {"shop": "psyoung82"}), ("food_losses", {}),
                        ("realized_by_shop", {"kind": "food"}), ("class_ladder", {}), ("buyer_profile", {"name": "move_76"}),
                        ("shop_profile", {"name": "nocha12"}), ("score", {})):
            r = call(n, args)
            print(n, "error" if r.get("isError") else "ok", len(r["content"][0]["text"]), r["content"][0]["text"][:140])
        print("config", write_config())
    else:
        serve()
