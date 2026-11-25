"""MCP tools the buyer's model can call while it picks a shop (stdio server, MCP Python SDK 2.x).

    claude -p ... --mcp-config <config naming this file> --strict-mcp-config --allowedTools "mcp__market__*"

Instead of pasting every review, every past trade and the whole forum into the prompt, the prompt carries one
line per candidate and the model looks up what it needs. Every call is logged to logs/tool_calls.jsonl.

Environment (set by market_buyer_r3.py in the MCP config):
    SHOPS_FILE        this order's shops.json (reviews and offers)
    MARKET_DIR        the kit's .market/ folder (forum.json, catalog.json)
    BUYER_MEMORY_R3   my buyer's memory file
"""
import json
import os
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from r3lib import HERE, MARKET_SALES, append_jsonl, catalog, forum_posts, read_json

mcp = MCPServer("market")
LOG = HERE / "logs" / "tool_calls.jsonl"


def _log(tool, args, out):
    append_jsonl(LOG, {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "tool": tool, "args": args, "result": out[:300]})
    return out


def _shops():
    return read_json(os.environ.get("SHOPS_FILE", ""), []) or []


@mcp.tool()
def shop_record(shop: str) -> str:
    """My own record of a shop (by name): orders, settled deals, total gain, grade claims that were false,
    goods that broke, payments refused for missing certificate or shelf life, and errors."""
    from market_buyer_r3 import mem_text, shop_memory      # imported here: the buyer module is not needed otherwise
    return _log("shop_record", {"shop": shop}, f"{shop}: {mem_text(shop_memory().get(shop))}")


@mcp.tool()
def shop_reviews(shop: str) -> str:
    """The newest reviews of a shop (by name) in the market, as listed for this order."""
    s = next((x for x in _shops() if x.get("name") == shop), None)
    if not s:
        return _log("shop_reviews", {"shop": shop}, f"{shop} is not in this order's shop list")
    rv = s.get("reviews") or []
    lines = [f"{r.get('stars')} stars, round {r.get('round')}: {str(r.get('review', ''))[:120]}" for r in rv[:6]]
    return _log("shop_reviews", {"shop": shop}, "\n".join(lines) or "no reviews")


@mcp.tool()
def sku_market(sku: str) -> str:
    """What the whole market did with one SKU: settled deals, recent prices, grades shipped; plus its list price,
    kind and shelf life from the catalog."""
    c = catalog().get(sku)
    p = (read_json(MARKET_SALES, {}) or {}).get("skus", {}).get(sku)
    head = f"{sku}: {c['name'][:60]}, list {c['list_price']}, {c['kind']}" + (
        f", keeps {c['shelf_hours']} h" if c and c.get("shelf_hours") else "") if c else f"{sku}: not in the catalog"
    body = (f"; settled {p['n']} times, recent prices {p['amounts'][-5:]}, grades {p['grades']}" if p
            else "; no settled deals seen yet")
    return _log("sku_market", {"sku": sku}, head + body)


@mcp.tool()
def forum_search(query: str) -> str:
    """Forum posts that contain every word of the query (shop names, item names, 'grade', '거짓' ...), best voted first."""
    words = [w.lower() for w in query.split() if w]
    hits = [p for p in forum_posts() if all(w in str(p.get("text", "")).lower() for w in words)]
    hits.sort(key=lambda p: -(p.get("score") or 0))
    lines = [f"#{p.get('id')} by {p.get('author')} ({p.get('role')}, votes {p.get('score')}): {str(p.get('text', ''))[:160]}"
             for p in hits[:5]]
    return _log("forum_search", {"query": query}, "\n".join(lines) or "no posts")


if __name__ == "__main__":
    mcp.run()
