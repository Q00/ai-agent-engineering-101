"""Summarise one live trial of the slow loops from the host, standard library only.

Runs on the VPS host (no pydantic there): reads the events ledger, the policy and
picture files and the kit log, and prints one JSON object. Nothing is changed.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
BANNER = "market_kit 4"
LOW_TRUST = 0.5
COUNTERS = {
    "orders": "[order ",
    "verified": "verified True",
    "buyer_reads": "[R3 BUYER] read",
    "seller_reads": "seller.read",
    "screened": "screened",
    "refused_openings": "seller.refused",
    "paid": "[acp] paid",
    "not_paid": "not paying",
}


def _json(path: Path) -> dict[str, object]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def ledger_summary(events: Path, since: float) -> dict[str, object]:
    """Model calls, spend and policy changes since ``since`` (epoch seconds)."""
    if not events.exists():
        return {"events": "missing"}
    day = datetime.fromtimestamp(time.time(), UTC).astimezone(KST).date().isoformat()
    with closing(sqlite3.connect(events, timeout=10)) as db:
        calls = [
            {
                "role": role,
                "model": model,
                "calls": n,
                "accepted": acc,
                "avg_latency_ms": round(lat or 0),
                "max_latency_ms": mx,
                "cost_usd": round(cost or 0, 5),
            }
            for role, model, n, acc, lat, mx, cost in db.execute(
                """SELECT role, model, count(*), sum(accepted), avg(latency_ms),
                max(latency_ms), sum(cost_usd) FROM model_decisions
                WHERE ts >= ? GROUP BY role, model""",
                (since,),
            )
        ]
        spend = db.execute(
            "SELECT usd, calls FROM model_spend WHERE day = ?", (day,)
        ).fetchone()
        changes = [
            {
                "ts": datetime.fromtimestamp(ts, KST).strftime("%H:%M:%S"),
                "actor": actor,
                "layer": layer,
                "field": field,
                "before": before,
                "after": after,
                "accepted": bool(acc),
                "note": note,
                "reason": (reason or "")[:100],
            }
            for ts, actor, layer, field, before, after, reason, acc, note in db.execute(
                """SELECT ts, actor, layer, field, before, after, reason, accepted, note
                FROM policy_changes WHERE ts >= ? ORDER BY ts""",
                (since,),
            )
        ]
        attempts = dict(
            db.execute(
                "SELECT outcome, count(*) FROM buyer_attempts WHERE ts >= ? "
                "GROUP BY outcome",
                (since,),
            ).fetchall()
        )
        receipts = db.execute(
            "SELECT count(*) FROM seller_receipts WHERE ts >= ?", (since,)
        ).fetchone()[0]
    return {
        "model_calls": calls,
        "spend_today_usd": round(spend[0], 5) if spend else 0.0,
        "spend_today_calls": spend[1] if spend else 0,
        "policy_changes": changes,
        "buyer_attempts": attempts,
        "seller_receipts": receipts,
    }


def policy_summary(policy: Path) -> dict[str, object]:
    """Version, layers and bands of the policy file."""
    history = _json(policy)
    if not history:
        return {"policy": "defaults (no file)"}
    current = history.get("current", {})
    strategy = history.get("strategy")
    return {
        "version": current.get("version") if isinstance(current, dict) else None,
        "reason": current.get("reason") if isinstance(current, dict) else None,
        "strategy_version": strategy.get("version")
        if isinstance(strategy, dict)
        else None,
        "tactical": history.get("tactical"),
        "bands": history.get("bands", {}),
    }


def picture_summary(picture: Path) -> dict[str, object]:
    """Who wrote the picture and what it holds."""
    data = _json(picture)
    raw = data.get("shops", [])
    shops = [s for s in raw if isinstance(s, dict)] if isinstance(raw, list) else []
    return {
        "by": data.get("by"),
        "written_at": data.get("written_at"),
        "shops": len(shops),
        "low_trust": [
            {"name": s.get("name"), "trust": s.get("trust"), "flags": s.get("flags")}
            for s in shops
            if float(str(s.get("trust", 1))) < LOW_TRUST
        ][:10],
        "hot_categories": data.get("hot_categories"),
        "alerts": data.get("alerts"),
        "notes": data.get("notes"),
    }


def kit_log_summary(log: Path) -> dict[str, int]:
    """Count trade events after the last kit banner (the trial's own relaunch)."""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    start = 0
    for index, line in enumerate(lines):
        if BANNER in line:
            start = index
    window = lines[start:]
    return {
        key: sum(1 for line in window if needle in line)
        for key, needle in COUNTERS.items()
    }


def summarize(data: Path, since: float, kit_log: Path | None) -> dict[str, object]:
    """Collect every part into one report."""
    report: dict[str, object] = {
        "since_utc": datetime.fromtimestamp(since, UTC).isoformat(timespec="seconds"),
        "now_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        **ledger_summary(data / "r3-events.sqlite", since),
        "policy": policy_summary(data / "r3-policy.json"),
        "picture": picture_summary(data / "r3-picture.json"),
    }
    if kit_log is not None:
        report["kit_log"] = kit_log_summary(kit_log)
    return report


def main() -> None:
    """CLI: --data DIR --since EPOCH [--kit-log FILE]."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--data", type=Path, required=True)
    _ = parser.add_argument("--since", type=float, required=True)
    _ = parser.add_argument("--kit-log", type=Path, default=None)
    args = parser.parse_args()
    print(  # noqa: T201 - CLI output
        json.dumps(
            summarize(args.data, args.since, args.kit_log), ensure_ascii=False, indent=1
        )
    )


if __name__ == "__main__":
    main()
