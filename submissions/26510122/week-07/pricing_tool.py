"""Single-purpose, read-only MCP tool for recorded seller price statistics.

The database path is supplied by the operator, never by the model. No SQL or
individual transaction rows are exposed through the tool interface.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pricing import PricingRecorder


TOOL_NAME = "get_price_stats"
ITEMS = ("lamp", "chair", "bookshelf")


def get_price_stats(db_path: str | Path, item: str) -> dict:
    if not isinstance(item, str) or item not in ITEMS:
        raise ValueError("item must be lamp, chair, or bookshelf")
    groups = PricingRecorder.readonly_stats(db_path, item)
    return {
        "item": item,
        "source": "local seller records; historical log-only trades excluded",
        "by_policy": [
            {
                "policy": row["arm"],
                "attempts": row["attempts"],
                "opening_asks_sent": row["exposed"],
                "receipts_issued": row["receipts"],
                "market_counted": row["counted"],
                "rejected": row["rejected"],
                "timed_out": row["timed_out"],
                "infrastructure_errors": row["infra_error"],
                "negotiation_unresolved": row["unresolved"],
                "receipts_pending_market": row["receipt_pending"],
                "receipts_not_counted": row["receipt_not_counted"],
                "counted_margin_total": row["margin_counted"],
                "average_opening_ask_over_reserve": (
                    round(row["opening_ratio_sum"] / row["exposed"], 4)
                    if row["exposed"] else None
                ),
            }
            for row in groups
        ],
        "caution": "Unknown and pending outcomes are not failed deals; these observational records do not prove a price effect.",
    }


def dispatch(message: dict, db_path: Path) -> dict | None:
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    if method == "initialize":
        requested = message.get("params", {}).get("protocolVersion", "2025-06-18")
        result = {
            "protocolVersion": requested,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "seller-pricing-stats", "version": "1.0.0"},
            "instructions": "Only get_price_stats is available. It summarizes local seller observations; never infer that pending or unresolved trades failed. No SQL or transaction records are exposed.",
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{
            "name": TOOL_NAME,
            "description": "Read aggregate seller outcomes for one item. No SQL input or individual trades. Historical log-only transactions are excluded.",
            "inputSchema": {
                "type": "object",
                "properties": {"item": {"type": "string", "enum": list(ITEMS)}},
                "required": ["item"],
                "additionalProperties": False,
            },
            "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True},
        }]}
    elif method == "tools/call":
        params = message.get("params", {})
        arguments = params.get("arguments", {})
        if params.get("name") != TOOL_NAME or not isinstance(arguments, dict) or set(arguments) != {"item"}:
            result = {"isError": True, "content": [{"type": "text", "text": "Unknown tool or invalid arguments"}]}
        else:
            try:
                data = get_price_stats(db_path, arguments["item"])
                result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                          "structuredContent": data}
            except (ValueError, OSError) as error:
                result = {"isError": True, "content": [{"type": "text", "text": type(error).__name__ + ": " + str(error) if isinstance(error, ValueError) else "Database unavailable"}]}
            except Exception:
                result = {"isError": True, "content": [{"type": "text", "text": "Statistics unavailable"}]}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    for line in sys.stdin:
        try:
            reply = dispatch(json.loads(line), args.db)
            if reply is not None:
                print(json.dumps(reply, ensure_ascii=False), flush=True)
        except (ValueError, TypeError):
            print(json.dumps({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "Parse error"}}), flush=True)


if __name__ == "__main__":
    main()
