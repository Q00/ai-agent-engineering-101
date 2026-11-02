"""The data room: our MCP server (Model Context Protocol, week 5), built with the official SDK.

    python data_room.py --as seller|buyer|integrated      (stdio)

The analysis runner starts one for each analysis (mcp_host.py) and lends its tools to the model.
Any other MCP host can use it too, e.g. Claude Code over ssh during the round, to ask the same
questions by hand. What it serves, all read from the files the trading modules and the runner
write (MARKET_DIR):

    tools       ledger_query, replay, shop_credit, leave_memo (tools.py holds the logic and limits)
    resources   kiro://business   our business now (the briefing's part 1)
                kiro://insights   the active insights
                kiro://reports    the latest report of each role analysis
                kiro://memos      the memos of the last 24 hours

--as says which analysis is calling; it is the memo's sender, so a model cannot sign as another.
"""
import argparse
import json
import time

from mcp.server.fastmcp import FastMCP

import common
import context
import insights
import tools

room = FastMCP("kiro729-data-room")
CALLER = {"role": "integrated", "memos": 0}


@room.tool()
def ledger_query(category: str | None = None, sku: str | None = None, shop: str | None = None, hours: float = 24) -> str:
    """Counts from the market's public ledger over the last `hours` hours (1 to 72, default 24), for one
    category (its name as in the catalog, e.g. 김치), one SKU, or one shop: settled deals, the average price
    paid as a share of the list price, grades sold, grade claims and how many were true, goods that broke
    after the sale, and the shops that sold the most. Give at most one of category, sku or shop; none
    gives the whole market."""
    return tools.run("ledger_query", {"category": category, "sku": sku, "shop": shop, "hours": hours})


@room.tool()
def replay(field: str, value: float | dict[str, float], hours: float = 24) -> str:
    """What a rule change would have done to the last `hours` hours of our own deals. field is one of
    seller.price_adjust (a number), seller.warranty_extra ({"A": .., "B": .., "C": ..}) or buyer.max_ratio
    (a number); value is the proposed value. Our own negotiations are run again with the shop's own
    negotiation rule, each buyer making the offers it made; the answer gives the recorded and the
    replayed deals and money, the assumption, and how many recorded outcomes the replay gives back
    with the rules in force (how far to trust it)."""
    return tools.run("replay", {"field": field, "value": value, "hours": hours})


@room.tool()
def shop_credit(shop: str) -> str:
    """One shop's record, by its name or address: its deals, grade claims (and how many were true) and
    breakdowns in the public ledger, what our buyer saw there (outcomes, false claims, breakdowns, the
    price share paid), the trust our buyer gives it, and its reviews."""
    return tools.run("shop_credit", {"shop": shop})


@room.tool()
def leave_memo(to: str, act: str, text: str, evidence: str = "") -> str:
    """Leave a memo for another analysis; it appears in their next briefing (part 4). to: seller, buyer,
    integrated or all (not yourself). act: inform (a fact they should know), propose (a change they
    could make) or request (something to look into). text: at most 300 characters; evidence: the
    numbers, at most 200. At most 2 memos per analysis."""
    return tools.run("leave_memo", {"to": to, "act": act, "text": text, "evidence": evidence},
                     role=CALLER["role"], state=CALLER)


@room.resource("kiro://business", mime_type="application/json")
def business() -> str:
    """Our business now: phase, balance, stock, the last 24 hours of sales and purchases, the rules in force."""
    return context.compact(context.business(time.time()))


@room.resource("kiro://insights", mime_type="application/json")
def active_insights() -> str:
    """The active insights (testing or confirmed), each with its finding, evidence and use."""
    return json.dumps(insights.active(insights.load()), ensure_ascii=False)


@room.resource("kiro://reports", mime_type="application/json")
def latest_reports() -> str:
    """The latest report of the seller analysis and of the buyer analysis, with their proposals."""
    rows = common.read_jsonl(common.MARKET_DIR / "analysis" / "reports.jsonl")
    latest = {r.get("role"): {k: r.get(k) for k in ("at", "report", "proposals")} for r in rows}
    return json.dumps(latest, ensure_ascii=False)


@room.resource("kiro://memos", mime_type="application/json")
def recent_memos() -> str:
    """The memos the analyses left for each other in the last 24 hours."""
    since = time.time() - 86400
    return json.dumps([m for m in common.read_jsonl(common.MARKET_DIR / "analysis" / "memos.jsonl")
                       if (m.get("ts") or 0) >= since], ensure_ascii=False)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as", dest="role", choices=("seller", "buyer", "integrated"), default="integrated")
    CALLER["role"] = ap.parse_args().role
    room.run("stdio")


if __name__ == "__main__":
    main()
