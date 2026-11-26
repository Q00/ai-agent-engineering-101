"""Read-only analyst between the operator's two-hour checks (owner, 11/12 22:40).

The owner asked for another model to watch the market while the operator is away:
"분석한다던지, 동태 살핀다던지 시장상황본다던지, 쌓인로그 모두본다던지". Every
30 minutes code reduces the board, the public ledger, our event log and the kit log
to one small digest and compares it with the previous one; code alone raises the
flags. A model (the strategy role, so Sonnet and then the OpenAI fallback, under
the shared dollar cap) reads the digest and writes a short Korean note. Nothing here
trades or edits a policy: on 11/10 a model with that power proposed 166 changes in
72 h, every one in a direction already measured to lower the total. Urgent flags
reach Telegram through the host wrapper; everything lands in a history file the
operator reads at the next check.
"""

from __future__ import annotations

import json
import sqlite3
import statistics
import time
from collections import Counter
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Final
from urllib.request import urlopen

import anyio
import typer

from market_a2a.anthropic_fallback import HAIKU_MODEL
from r3.events import EventLedger
from r3.model_client import (
    SONNET_MODEL,
    ModelClient,
    Request,
    parse_arguments,
    tool_param,
)
from r3.standing import standing

MARKET: Final = "https://market.ourolabs.ai"
BUYER: Final = "25620027"
PLAYER: Final = "SungJinho"
OUR_SHOP: Final = "https://ax-week07.treasuresky.xyz"
KST: Final = timezone(timedelta(hours=9))
STATE_FILE: Final = "r3-analyst-state.json"
LATEST_FILE: Final = "r3-analyst-latest.json"
HISTORY_FILE: Final = "r3-analyst-history.jsonl"
GATE: Final = "AX_R3_MANAGER_MODEL"
STRATEGY_MODEL_ENV: Final = "AX_R3_STRATEGY_MODEL"
# Flag thresholds (code decides; the model only explains).
BUYS_MIN_PER_H: Final = 50
SHARE_LINE: Final = 0.75
SALES_DROP: Final = 0.5
RIVAL_JUMP: Final = 0.02
SCORING_JUMP: Final = 0.02
MIN_ACTIVE_DEALS: Final = 100
ALERT_REPEAT_SECONDS: Final = 3600.0
FACTS: Final = (
    "Class total = shop ALL + buyer ALL over student percentiles. Shop: 0.5 book "
    "(cash + stock at cost) + 0.3 revenue + 0.2 stars_rank. Buyer: 0.5 Saved + 0.3 "
    "forum (raw value) + 0.1 deals + 0.1 posts. Patch 3.6 (11/12 18:19, the last "
    "Round 3 rule change): Saved per deal = (list - paid) / (list - unit cost), 1 at "
    "or under cost, -1 useless, 0 refunded, over (deals + 100). Our buyer accepts a "
    "deal only when its expected share is at least 0.75. Every goods break lands "
    "within 6 h of sale; our B and C goods carry no warranty since 11/12 22:07, A "
    "keeps 72 h. The deadline is 11/17 23:59 KST. You only read and explain; you "
    "cannot trade or change anything."
)
PROMPT: Final = (
    "You watch an agent market between the operator's checks. Read the digest and "
    "the code's flags, then write in plain Korean for a shop owner: one headline, "
    "at most three findings that each cite numbers from the digest, and at most "
    "three concrete things the operator should check next. Say what moved and why "
    "it matters for first place; never invent numbers; say 'unknown' when the "
    "digest lacks something. Answer only by calling the write_note tool, never "
    "in plain text. Facts: " + FACTS
)


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)


def _num(value: object) -> float | None:
    try:
        out = float(str(value))
    except ValueError:
        return None
    return out if out == out else None  # noqa: PLR0124 - NaN check


def board_digest(board: dict[str, object]) -> dict[str, object]:
    """Class race: our total, first place, top-8 totals and the median Saved."""
    players = [
        p
        for p in board.get("players", [])  # type: ignore[union-attr]
        if isinstance(p, dict) and p.get("student") and not p.get("house")
    ]
    totals = sorted(
        (
            (float(p["student_total"]), str(p.get("player")))
            for p in players
            if _num(p.get("student_total")) is not None
        ),
        reverse=True,
    )
    ours = next((t for t, n in totals if n == PLAYER), None)
    rival = next(((t, n) for t, n in totals if n != PLAYER), (None, None))
    rank = next((i + 1 for i, (_, n) in enumerate(totals) if n == PLAYER), None)
    buyers = [b for b in board.get("buyers", []) if isinstance(b, dict)]  # type: ignore[union-attr]
    # Active buyers only: idle and house rows sit at zero and pin the median.
    saved = [
        v
        for b in buyers
        if (_num(b.get("deals")) or 0) >= MIN_ACTIVE_DEALS
        and (v := _num(b.get("saved"))) is not None
    ]
    score = standing(board, BUYER)
    parts = {
        str(c.get("metric")): {
            "percentile": c.get("percentile"),
            "value": c.get("value"),
            "rank": c.get("rank"),
        }
        for c in score.get("components", [])  # type: ignore[union-attr]
        if isinstance(c, dict)
    }
    return {
        "rank": rank,
        "of": len(totals),
        "total": ours,
        "rival": rival[1],
        "rival_total": rival[0],
        "gap1": round(rival[0] - ours, 4)
        if rival[0] is not None and ours is not None
        else None,
        "top8": {n: round(t, 4) for t, n in totals[:8]},
        "components": parts,
        "market_saved_median": round(statistics.median(saved), 4) if saved else None,
        "board_keys": sorted(str(k) for k in board),
    }


def ledger_digest(data: Path, now: float) -> dict[str, object]:
    """Compare our buys, sales, refunds and the market pace hour on hour."""
    catalog = json.loads((data / ".market/catalog.json").read_text(encoding="utf-8"))
    catalog = catalog.get("items", catalog)
    out: dict[str, object] = {}
    with closing(_connect(data / "r3-ledger.sqlite")) as db:
        buys = db.execute(
            "SELECT ts,sku,amount,data FROM rows "
            "WHERE kind='settle' AND buyer=? AND ts>=?",
            (BUYER, now - 7200),
        ).fetchall()
        wanted = {json.loads(d or "{}").get("unit") for *_, d in buys} - {None}
        costs: dict[str, float] = {}
        for (raw,) in db.execute(
            "SELECT data FROM rows WHERE kind='supply' AND ts>=?", (now - 10 * 86400,)
        ):
            lot = json.loads(raw or "{}")
            units = lot.get("units") or []
            for unit in wanted.intersection(units):
                costs[unit] = float(lot.get("total") or 0) / len(units)
        for label, low, high in (
            ("last_h", now - 3600, now),
            ("prev_h", now - 7200, now - 3600),
        ):
            shares: list[float] = []
            kinds: Counter[str] = Counter()
            for ts, sku, amount, raw in buys:
                if not low <= ts < high:
                    continue
                item = catalog.get(sku) or {}
                kinds[str(item.get("kind"))] += 1
                price, cost = (
                    item.get("list_price"),
                    costs.get(json.loads(raw or "{}").get("unit")),
                )
                if price and cost and cost < price and amount is not None:
                    shares.append(
                        1.0 if amount <= cost else (price - amount) / (price - cost)
                    )
            out[f"buys_{label}"] = {
                "count": sum(kinds.values()),
                "goods": kinds["goods"],
                "food": kinds["food"],
                "share_mean": round(statistics.mean(shares), 3) if shares else None,
                "share_below_line": sum(s < SHARE_LINE for s in shares),
            }
        sales = db.execute(
            "SELECT ts,amount,grade,data,sku FROM rows WHERE kind='settle' "
            "AND rtrim(shop,'/')=? AND ts>=?",
            (OUR_SHOP, now - 86400),
        ).fetchall()
        hourly = Counter(int((now - ts) // 3600) for ts, *_ in sales)
        out["sales_last_h"] = hourly[0]
        out["sales_prev_h"] = hourly[1]
        out["sales_24h_median_per_h"] = statistics.median(hourly[h] for h in range(24))
        out["revenue_last_h"] = sum(a or 0 for ts, a, *_ in sales if ts >= now - 3600)
        grades: Counter[str] = Counter()
        for ts, _, grade, raw, sku in sales:
            if ts >= now - 7200 and (catalog.get(sku) or {}).get("kind") == "goods":
                hours = json.loads(raw or "{}").get("warranty_hours") or 0
                grades[f"{grade}_{'warranty' if hours else 'none'}"] += 1
        out["goods_sales_2h_by_grade"] = dict(grades)
        refunds = db.execute(
            "SELECT count(*),sum(json_extract(data,'$.refunded')) FROM rows "
            "WHERE kind='broken' AND ts>=? "
            "AND rtrim(json_extract(data,'$.shop'),'/')=?",
            (now - 3600, OUR_SHOP),
        ).fetchone()
        out["refunds_last_h"] = {"count": refunds[0], "credits": refunds[1] or 0}
        market = db.execute(
            "SELECT sum(ts>=?),sum(ts<?) FROM rows WHERE kind='settle' AND ts>=?",
            (now - 3600, now - 3600, now - 7200),
        ).fetchone()
        out["market_settles"] = {"last_h": market[0] or 0, "prev_h": market[1] or 0}
    return out


def events_digest(data: Path, now: float) -> dict[str, object]:
    """Why our orders failed, last hour against the one before, and model spend."""

    def bucket(reason: str) -> str:
        r = reason.lower()
        for key, name in (
            ("stock", "no_stock"),
            ("emergency line", "price_wall"),
            ("grade c", "grade_c"),
            ("credit below", "receipt_line"),
            ("timeout", "timeout"),
            ("timed out", "timeout"),
            ("directive", "injection"),
            ("rejected", "rejected"),
        ):
            if key in r:
                return name
        return "other"

    out: dict[str, object] = {}
    with closing(_connect(data / "r3-events.sqlite")) as db:
        for label, low, high in (
            ("last_h", now - 3600, now),
            ("prev_h", now - 7200, now - 3600),
        ):
            rows = db.execute(
                "SELECT outcome,reason FROM buyer_attempts WHERE ts>=? AND ts<?",
                (low, high),
            ).fetchall()
            fails = Counter(bucket(r or "") for o, r in rows if o != "agreed")
            out[f"attempts_{label}"] = {
                "tries": len(rows),
                "agreed": sum(o == "agreed" for o, _ in rows),
                "fails": dict(fails.most_common(6)),
            }
        orders = db.execute(
            "SELECT count(*) FROM buyer_orders WHERE observed_at>=?", (now - 3600,)
        ).fetchone()
        out["orders_last_h"] = orders[0]
    day = datetime.fromtimestamp(now, KST).date().isoformat()
    usd, calls = EventLedger(data / "r3-events.sqlite").spend(day)
    out["model_spend_today"] = {"usd": round(usd, 3), "calls": calls}
    return out


def log_digest(data: Path, lines: int = 20000) -> dict[str, int]:
    """Count trouble words in the tail of the kit log."""
    path = data / "logs/market-kit.log"
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - lines * 220))
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return {}
    return {
        "traceback": tail.count("Traceback"),
        "suspicious": tail.count("outcome=suspicious"),
        "model_failed": tail.count("model_failed"),
        "hung_skips": tail.count("hung_shops skipped"),
    }


def flags(  # noqa: C901 - one rule per block
    current: dict[str, object], previous: dict[str, object]
) -> list[dict[str, str]]:
    """Code-decided flags; ``urgent`` ones may reach Telegram."""
    out: list[dict[str, str]] = []
    board, before = current.get("board") or {}, previous.get("board") or {}
    ledger = current.get("ledger") or {}
    logs = current.get("logs") or {}
    if not (
        isinstance(board, dict)
        and isinstance(before, dict)
        and isinstance(ledger, dict)
    ):
        return out
    median, old_median = (
        board.get("market_saved_median"),
        before.get("market_saved_median"),
    )
    if (
        median is not None
        and old_median is not None
        and abs(median - old_median) >= SCORING_JUMP
    ):
        out.append(
            {
                "key": "scoring_change",
                "level": "urgent",
                "text": (
                    f"시장 전체 Saved 중앙값이 {old_median}→{median}로 한 번에 "
                    "움직였습니다. 점수 규칙 변경(패치)일 수 있습니다."
                ),
            }
        )
    if before.get("board_keys") and board.get("board_keys") != before.get("board_keys"):
        out.append(
            {
                "key": "board_shape",
                "level": "urgent",
                "text": "보드 항목 구성이 바뀌었습니다. 공지·규칙 변경을 확인하세요.",
            }
        )
    if (
        board.get("rank") is not None
        and before.get("rank") is not None
        and board["rank"] != before["rank"]
    ):
        level = "urgent" if board["rank"] == 1 or before["rank"] == 1 else "watch"
        out.append(
            {
                "key": "rank_move",
                "level": level,
                "text": f"학생 순위 {before['rank']}위→{board['rank']}위.",
            }
        )
    top, old_top = board.get("top8") or {}, before.get("top8") or {}
    for name, total in top.items():
        old = old_top.get(name)
        if name != PLAYER and old is not None and total - old >= RIVAL_JUMP:
            out.append(
                {
                    "key": f"rival_jump:{name}",
                    "level": "watch",
                    "text": f"{name} 총점 {old}→{total} (+{total - old:.3f}).",
                }
            )
    buys = ledger.get("buys_last_h") or {}
    if isinstance(buys, dict):
        if (buys.get("count") or 0) < BUYS_MIN_PER_H:
            out.append(
                {
                    "key": "buys_low",
                    "level": "urgent",
                    "text": (
                        f"최근 1시간 구매 {buys.get('count')}건(기준 {BUYS_MIN_PER_H})."
                    ),
                }
            )
        share = buys.get("share_mean")
        if share is not None and share < SHARE_LINE:
            out.append(
                {
                    "key": "share_low",
                    "level": "urgent",
                    "text": f"거래당 Saved 평균 {share}(기준 {SHARE_LINE}).",
                }
            )
    median_sales = ledger.get("sales_24h_median_per_h")
    if median_sales and (ledger.get("sales_last_h") or 0) < SALES_DROP * median_sales:
        out.append(
            {
                "key": "sales_low",
                "level": "watch",
                "text": (
                    f"최근 1시간 판매 {ledger.get('sales_last_h')}건"
                    f"(24시간 시간당 중앙값 {median_sales})."
                ),
            }
        )
    if isinstance(logs, dict) and logs.get("traceback"):
        out.append(
            {
                "key": "traceback",
                "level": "urgent",
                "text": f"로그 끝부분에 Traceback {logs['traceback']}건.",
            }
        )
    return out


def telegram_text(
    note: dict[str, object], raised: list[dict[str, str]], now: float
) -> str:
    """Short phone message for urgent flags, with the model's headline."""
    stamp = datetime.fromtimestamp(now, KST).strftime("%m/%d %H:%M")
    lines = [f"🧠 AX 분석가 · {stamp}"]
    lines += [f"⚠ {f['text']}" for f in raised]
    headline = note.get("headline")
    if headline:
        lines += ["", str(headline)]
    for finding in (note.get("findings") or [])[:3]:  # type: ignore[union-attr]
        lines.append(f"• {finding}")
    return "\n".join(lines)[:1500]


def note_schema() -> dict[str, object]:
    """Return the schema of the one tool the model may call."""
    return {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "One Korean sentence."},
            "findings": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "checks": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
            "urgency": {"type": "string", "enum": ["none", "watch", "act"]},
        },
        "required": ["headline", "findings", "checks", "urgency"],
    }


async def explain(data: Path, digest: dict[str, object]) -> dict[str, object]:
    """Ask the strategy model for a short note; empty when the gate or key is closed."""
    client = ModelClient.from_env(EventLedger(data / "r3-events.sqlite"), gate=GATE)
    if client is None:
        return {}
    import os  # noqa: PLC0415 - read once per run

    # Sonnet with thinking may answer in prose instead of the tool; Haiku is
    # forced to call it, so it writes the note when Sonnet did not.
    for model in (os.environ.get(STRATEGY_MODEL_ENV, SONNET_MODEL), HAIKU_MODEL):
        reply = await client.call(
            Request(
                role="strategy",
                model=model,
                system=PROMPT,
                user=json.dumps(digest, ensure_ascii=False, default=str)[:30000],
                tool=tool_param(
                    "write_note", "Report what moved and what to check.", note_schema()
                ),
                max_tokens=1500,
                deadline_seconds=90,
                effort=None,
                summary="analyst",
            )
        )
        parsed = parse_arguments(reply.arguments) if reply is not None else None
        if isinstance(parsed, dict) and parsed.get("headline"):
            return {**parsed, "model": reply.model if reply is not None else model}
    return {}


def run(
    data: Path, *, now: float | None = None, use_model: bool = True
) -> dict[str, object]:
    """One pass: digest, flags, note, files; returns what the host should send."""
    stamp = time.time() if now is None else now
    state_path = data / STATE_FILE
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    with urlopen(f"{MARKET}/api/board", timeout=20) as reply:  # noqa: S310 - fixed https URL
        board = json.loads(reply.read())
    digest: dict[str, object] = {
        "ts": stamp,
        "board": board_digest(board),
        "ledger": ledger_digest(data, stamp),
        "events": events_digest(data, stamp),
        "logs": log_digest(data),
    }
    raised = flags(digest, state.get("digest") or {})
    digest["flags"] = raised
    note = anyio.run(explain, data, digest) if use_model else {}
    sent = dict(state.get("sent") or {})
    urgent = [
        f
        for f in raised
        if f["level"] == "urgent"
        and stamp - float(sent.get(f["key"], 0)) >= ALERT_REPEAT_SECONDS
    ]
    act = (
        note.get("urgency") == "act"
        and stamp - float(sent.get("model_act", 0)) >= ALERT_REPEAT_SECONDS
    )
    telegram = telegram_text(note, urgent, stamp) if urgent or act else ""
    for f in urgent:
        sent[f["key"]] = stamp
    if act:
        sent["model_act"] = stamp
    record = {
        "ts": stamp,
        "flags": raised,
        "note": note,
        "telegram": bool(telegram),
        "digest": digest,
    }
    (data / LATEST_FILE).write_text(
        json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8"
    )
    with (data / HISTORY_FILE).open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    state_path.write_text(
        json.dumps({"digest": digest, "sent": sent}, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    return {
        "telegram": telegram,
        "flags": len(raised),
        "urgent": len(urgent),
        "model": note.get("model", ""),
    }


def main(
    data: Annotated[Path, typer.Option("--dir")] = Path("/data/r3"),
    *,
    no_model: Annotated[bool, typer.Option("--no-model")] = False,
) -> None:
    """Print one JSON line for the host wrapper."""
    print(json.dumps(run(data, use_model=not no_model), ensure_ascii=False))  # noqa: T201


if __name__ == "__main__":
    _ = UTC
    typer.run(main)
