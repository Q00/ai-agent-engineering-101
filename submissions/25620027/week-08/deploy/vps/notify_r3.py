# ruff: noqa: E501, PLR2004, S603, S607, T201 - host script: Korean message lines, plain time
# constants (an hour, a day) and docker found on the cron PATH.
"""Host-side watcher: Telegram alerts and a daily summary through the Hermes gateway.

Runs from cron every five minutes on the VPS host, standard library only. It reads
the trading container's state, the kit log, the events ledger, the public-ledger
mirror, the private switch settings (two keys only, never secrets) and the public
board, decides what is worth a message, and hands the text to ``hermes send -t
telegram`` inside the existing Hermes container, which reuses the gateway's own
credentials. It never changes the trading run: no restart, no config write. Alerts
repeat at most every two hours while a condition holds and send one recovery line
when it clears. Messages are plain Korean text for reading on a phone.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.request import urlopen

KST = timezone(timedelta(hours=9))
BASE = Path("/docker/ax-week08-25620027")
CONTAINER = "ax-week08-25620027-r3-r3-1"
HERMES = "hermes-agent-ztmz-hermes-agent-1"
MARKET = "https://market.ourolabs.ai"
# Obsidian through the Hermes job queue: the Mac worker applies typed
# MAC_WORKER_APPROVED_OBSIDIAN jobs to the vault on the MacBook (contract:
# Codex_Obsidian_Bootstrap/JARVIS_JOB_REQUEST_CONTRACT.md).
JOBS = Path("/srv/hermes-jobs")
VAULT_NOTE = "20_학업/01_강의자료/AX이해와활용/261102_AX_R3_자문보고.md"
BUYER = "25620027"
# The switch now waits for the market to say round 3 (arm-r3.sh, every 2 min); an
# alert fires only if round 3 has been open this long without the switch.
SWITCH_GRACE = 600.0
# If round 3 has not opened by this time, the stop time must already be extended.
STOP_AT_CHECK = datetime(2026, 11, 3, 13, 30, tzinfo=UTC)  # 22:30 KST
ROUND_END = datetime(2026, 11, 17, 14, 59, 59, tzinfo=UTC)
STOP_SENDING = ROUND_END + timedelta(hours=1)
REPEAT_SECONDS = 7200.0
KIT_STALE_SECONDS = 900.0
LOOP_SILENT_SECONDS = 2400.0
SUMMARY_HOUR_KST = 9
# The 09 KST advisor run sends the morning summary together with its proposals;
# the five-minute pass only covers the summary if that run has not by 09:20.
SUMMARY_FALLBACK_MINUTE = 20
TITLES = {
    "container_down": "거래 컨테이너가 내려가 있습니다 (자동 복구가 3분마다 시도 중).",
    "kit_stale": "키트 로그가 15분 넘게 멈췄습니다. 거래가 멈췄을 수 있습니다.",
    "relaunch_loop": "지난 1시간에 자동 재기동이 3회 이상입니다. 재기동이 반복 실패하는 중일 수 있습니다.",
    "switch_missing": "라운드3가 열렸는데 10분 넘게 전환(관리자 켜기·종료 시각 연장)이 확인되지 않습니다.",
    "stop_at_risk": "종료 시각이 아직 11/3 23:59입니다. 이대로면 오늘 밤 거래가 멈춥니다.",
    "billing": "모델 API가 결제·크레딧 거절을 돌려 30분 휴지 중입니다. 거래는 규칙으로 계속됩니다.",
    "spend_cap": "오늘 모델 비용이 일일 상한에 닿았습니다. 전략이 Haiku로 강등됩니다.",
    "no_sales": "라운드3인데 지난 1시간 우리 가게·바이어 정산이 0건입니다(시장은 거래 중).",
    "loops_silent": "관리자 루프(정찰·전술·전략)가 40분 넘게 한 번도 돌지 않았습니다.",
}


@dataclass
class Snapshot:
    """Everything the watcher reads in one pass; plain values only."""

    now: float
    container_up: bool = False
    kit_age: float | None = None
    relaunches_1h: int = 0
    relaunches_24h: int = 0
    manager_on: bool = False
    stop_at: str = ""
    switch_done: bool = False
    # when the switch finished (the r3-switched marker's mtime); None if unknown
    switched_at: float | None = None
    daily_cap: float = 3.0
    spend_today: float = 0.0
    calls_today: int = 0
    cooldown: bool = False
    market_round: int | None = None
    shop_row: dict[str, object] = field(default_factory=dict)
    buyer_row: dict[str, object] = field(default_factory=dict)
    shop_rank: str = ""
    buyer_rank: str = ""
    settles_1h_market: int = 0
    shop_settles_1h: int = 0
    buyer_settles_1h: int = 0
    shop_settles_24h: int = 0
    buyer_settles_24h: int = 0
    roles_24h: dict[str, list[float]] = field(default_factory=dict)
    changes_24h: int = 0
    # manager loop runs of the last 24 h: (ts, role, outcome, detail); None when the
    # release predates the loop_runs table, so the silence alert stays off
    loop_runs: list[tuple[float, str, str, str]] | None = None
    # accepted policy changes of the last 24 h: (ts, actor, layer, field, before,
    # after, reason), oldest first
    policy_changes: list[tuple[float, str, str, str, str, str, str]] = field(
        default_factory=list
    )
    # engineer notes the strategy model filed (ts, note), oldest first
    engineer_notes: list[tuple[float, str]] = field(default_factory=list)
    refused_24h: int = 0
    balance: int | None = None
    units: int | None = None


def _kst(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, UTC).astimezone(KST)


def _env_keys(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """Read only the named keys from an env file; other lines are never kept."""
    found: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() in keys:
                found[name.strip()] = value.strip()
    except OSError:
        pass
    return found


def _sql(
    path: Path, query: str, args: tuple[object, ...] = ()
) -> list[tuple[object, ...]]:
    if not path.exists():
        return []
    try:
        with closing(
            sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        ) as db:
            return list(db.execute(query, args).fetchall())
    except sqlite3.Error:
        return []


def _rank(rows: list[dict[str, object]], name: str, key: str) -> str:
    players = [r for r in rows if not r.get("house")]
    players.sort(key=lambda r: float(str(r.get(key) or 0)), reverse=True)
    for index, row in enumerate(players, start=1):
        if str(row.get("name")) == name:
            return f"{index}/{len(players)}"
    return "?"


def collect(base: Path = BASE, *, market: str = MARKET) -> Snapshot:  # noqa: C901, PLR0912, PLR0915
    """Read the host, the ledgers and the public board once."""
    now = time.time()
    snap = Snapshot(now=now)
    ps = subprocess.run(
        ["docker", "ps", "--format", "{{.Names}}"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    snap.container_up = CONTAINER in ps.stdout.split()
    kit_log = base / "data/r3/logs/market-kit.log"
    if kit_log.exists():
        snap.kit_age = now - kit_log.stat().st_mtime
    health = base / "health-r3.log"
    if health.exists():
        for line in health.read_text(encoding="utf-8", errors="replace").splitlines()[
            -400:
        ]:
            # real relaunches only; the pre-fix "manager process missing" lines were
            # false positives of `docker top` (2026-11-01..02)
            if not line.startswith("== ") or not (
                "not running" in line or "restarting container" in line
            ):
                continue
            try:
                stamp = datetime.fromisoformat(line[3:23]).timestamp()
            except ValueError:
                continue
            snap.relaunches_1h += int(now - stamp <= 3600)
            snap.relaunches_24h += int(now - stamp <= 86400)
    env = _env_keys(
        base / "private/model-market.env", ("AX_R3_MANAGER_MODEL", "AX_R3_DAILY_USD")
    )
    snap.manager_on = env.get("AX_R3_MANAGER_MODEL") == "1"
    try:
        snap.daily_cap = float(env.get("AX_R3_DAILY_USD", "3"))
    except ValueError:
        snap.daily_cap = 3.0
    try:
        runtime = json.loads(
            (base / "private/runtime.json").read_text(encoding="utf-8")
        )
        snap.stop_at = str(runtime.get("stop_at", ""))
        public_url = str(runtime.get("public_url", "")).rstrip("/")
    except (OSError, ValueError):
        public_url = ""
    switch_log = base / "switch-r3.log"
    snap.switch_done = switch_log.exists() and "switch done" in switch_log.read_text(
        encoding="utf-8", errors="replace"
    )
    marker = base / "r3-switched"
    if marker.exists():
        snap.switched_at = marker.stat().st_mtime
    events = base / "data/r3/r3-events.sqlite"
    day = _kst(now).date().isoformat()
    spend = _sql(events, "SELECT usd, calls FROM model_spend WHERE day = ?", (day,))
    if spend:
        snap.spend_today, snap.calls_today = (
            float(str(spend[0][0])),
            int(str(spend[0][1])),
        )
    cool = _sql(events, "SELECT value FROM kv WHERE key = 'model_cooldown_until'")
    try:
        snap.cooldown = bool(cool) and float(str(cool[0][0])) > now
    except ValueError:
        snap.cooldown = False
    for role, n, acc, lat, cost in _sql(
        events,
        "SELECT role, count(*), sum(accepted), avg(latency_ms), sum(cost_usd) "
        "FROM model_decisions WHERE ts >= ? GROUP BY role",
        (now - 86400,),
    ):
        snap.roles_24h[str(role)] = [
            float(str(n)),
            float(str(acc or 0)),
            float(str(lat or 0)),
            float(str(cost or 0)),
        ]
    changes = _sql(
        events,
        "SELECT count(*) FROM policy_changes WHERE ts >= ? AND accepted = 1",
        (now - 86400,),
    )
    snap.changes_24h = int(str(changes[0][0])) if changes else 0
    snap.policy_changes = [
        (float(str(ts)), str(a), str(layer), str(f), str(b), str(af), str(r or ""))
        for ts, a, layer, f, b, af, r in _sql(
            events,
            "SELECT ts, actor, layer, field, before, after, reason FROM policy_changes "
            "WHERE ts >= ? AND accepted = 1 ORDER BY ts, id",
            (now - 86400,),
        )
    ]
    try:
        for line in (
            (base / "data/r3/r3-engineer-notes.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()[-200:]
        ):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and isinstance(row.get("note"), str):
                snap.engineer_notes.append((float(row.get("ts") or 0), row["note"]))
    except OSError:
        pass
    if _sql(events, "SELECT name FROM sqlite_master WHERE name = 'loop_runs'"):
        snap.loop_runs = [
            (float(str(ts)), str(role), str(outcome), str(detail or ""))
            for ts, role, outcome, detail in _sql(
                events,
                "SELECT ts, role, outcome, detail FROM loop_runs "
                "WHERE ts >= ? ORDER BY ts",
                (now - 86400,),
            )
        ]
    refused = _sql(
        events,
        "SELECT count(*) FROM payments WHERE ts >= ? AND paid = 0",
        (now - 86400,),
    )
    snap.refused_24h = int(str(refused[0][0])) if refused else 0
    ledger = base / "data/r3/r3-ledger.sqlite"
    rows = _sql(
        ledger,
        "SELECT ts, buyer, shop FROM rows WHERE kind = 'settle' AND ts >= ?",
        (now - 86400,),
    )
    for ts, buyer, shop in rows:
        recent = now - float(str(ts)) <= 3600
        ours_shop = bool(public_url) and str(shop).rstrip("/") == public_url
        ours_buyer = str(buyer) == BUYER
        snap.settles_1h_market += int(recent)
        snap.shop_settles_1h += int(recent and ours_shop)
        snap.buyer_settles_1h += int(recent and ours_buyer)
        snap.shop_settles_24h += int(ours_shop)
        snap.buyer_settles_24h += int(ours_buyer)
    try:
        inventory = json.loads(
            (base / "data/r3/.market/inventory.json").read_text(encoding="utf-8")
        )
        snap.balance = int(inventory.get("balance", 0))
        snap.units = len(inventory.get("units", []))
    except (OSError, ValueError, TypeError):
        pass
    try:
        with urlopen(f"{market}/api/board", timeout=20) as reply:  # noqa: S310 - fixed https URL
            board = json.loads(reply.read())
        snap.market_round = int(board.get("round") or 0)
        shops = [s for s in board.get("shops", []) if isinstance(s, dict)]
        buyers = [b for b in board.get("buyers", []) if isinstance(b, dict)]
        snap.shop_row = next((s for s in shops if str(s.get("name")) == BUYER), {})
        snap.buyer_row = next((b for b in buyers if str(b.get("name")) == BUYER), {})
        snap.shop_rank = _rank(shops, BUYER, "profit")
        snap.buyer_rank = _rank(buyers, BUYER, "saved")
    except (OSError, ValueError, TypeError):
        pass
    return snap


def conditions(snap: Snapshot) -> set[str]:
    """Alert keys that hold right now."""
    active: set[str] = set()
    if not snap.container_up:
        active.add("container_down")
    if snap.kit_age is not None and snap.kit_age > KIT_STALE_SECONDS:
        active.add("kit_stale")
    if snap.relaunches_1h >= 3:
        active.add("relaunch_loop")
    switched = (
        snap.manager_on and snap.stop_at.startswith("2026-11-17") and snap.switch_done
    )
    if snap.market_round == 3 and not switched:
        active.add("switch_missing")
    if snap.now >= STOP_AT_CHECK.timestamp() and snap.stop_at.startswith("2026-11-03"):
        active.add("stop_at_risk")
    if snap.cooldown:
        active.add("billing")
    if snap.spend_today >= snap.daily_cap:
        active.add("spend_cap")
    if (
        snap.market_round == 3
        and snap.settles_1h_market >= 20
        and snap.shop_settles_1h == 0
        and snap.buyer_settles_1h == 0
    ):
        active.add("no_sales")
    if (
        snap.loop_runs is not None
        and snap.market_round == 3
        and snap.manager_on
        and snap.switch_done
        # loops get their first turns after the switch (2026-11-03 21:05 false alarm)
        and (
            snap.switched_at is None
            or snap.now - snap.switched_at >= LOOP_SILENT_SECONDS
        )
        and not any(snap.now - run[0] <= LOOP_SILENT_SECONDS for run in snap.loop_runs)
    ):
        active.add("loops_silent")
    return active


ROLE_NAMES = {"scout": "정찰", "tactical": "전술", "strategy": "전략", "trade": "통역"}
OUTCOME_NAMES = {
    "read": "판독",
    "changed": "변경",
    "nudged": "조건 발동 변경",
    "held": "유지",
    "refused": "거부",
    "failed": "실패",
    "noted": "메모",
}
LOOP_ORDER = ("scout", "tactical", "strategy")


def loop_counts(runs: list[tuple[float, str, str, str]]) -> list[str]:
    """One line per loop: how often it ran and what it ended with."""
    lines = []
    for role in LOOP_ORDER:
        mine = [run for run in runs if run[1] == role]
        if not mine:
            lines.append(f"{ROLE_NAMES[role]} 0회")
            continue
        tally: dict[str, int] = {}
        for run in mine:
            tally[run[2]] = tally.get(run[2], 0) + 1
        parts = " · ".join(
            f"{OUTCOME_NAMES.get(k, k)} {v}" for k, v in sorted(tally.items())
        )
        lines.append(f"{ROLE_NAMES[role]} {len(mine)}회 ({parts})")
    return lines


def loop_digest(snap: Snapshot) -> str:
    """Hourly report: counts per loop, every change, and the latest hold reasons."""
    runs = [r for r in snap.loop_runs or [] if snap.now - r[0] <= 3600]
    lines = [
        "[AX R3] 지난 1시간 관리자 루프",
        f"{_kst(snap.now):%m/%d %H:%M} KST",
        "",
        *loop_counts(runs),
    ]
    moves = [r for r in runs if r[2] in {"changed", "nudged"}]
    if moves:
        lines += ["", "■ 바꾼 것"]
        lines += [
            f"{_kst(r[0]):%H:%M} {ROLE_NAMES.get(r[1], r[1])}: {r[3][:160]}"
            for r in moves[-5:]
        ]
    holds = [r for r in runs if r[2] in {"held", "refused", "failed"}]
    if holds:
        lines += ["", "■ 안 바꾼 이유(최근)"]
        lines += [
            f"{_kst(r[0]):%H:%M} {ROLE_NAMES.get(r[1], r[1])} "
            f"{OUTCOME_NAMES.get(r[2], r[2])}: {r[3][:160]}"
            for r in holds[-3:]
        ]
    scouts = [r for r in runs if r[1] == "scout" and r[2] == "read"]
    if scouts:
        lines += ["", f"■ 정찰 최근: {scouts[-1][3][:160]}"]
    return "\n".join(lines)


# What each policy field means, for a phone reader: label, how to show the
# value, and one line on the effect.
FIELD_KO: dict[str, tuple[str, str, str]] = {
    "buyer.quick_accept_ratio": (
        "바이어 즉시 수락선",
        "%",
        "정가의 이 비율 이하 호가는 흥정 없이 바로 삼",
    ),
    "buyer.first_offer_ratio": ("바이어 첫 제안", "%", "정가의 이 비율로 첫 값을 부름"),
    "buyer.late_accept_share": (
        "바이어 늦은 수락 전환점",
        "%",
        "예산이 이만큼 쓰이면 상한 안 호가는 다 받음",
    ),
    "buyer.max_price_ratio": (
        "바이어 지불 상한",
        "%",
        "정가의 이 비율 넘게는 절대 안 냄",
    ),
    "buyer.order_seconds": ("주문당 협상 시간", "초", "이 시간 안에 못 끝내면 포기"),
    "buyer.explore_rate": (
        "탐색 비율",
        "%",
        "이 비율의 주문은 1등 가게 대신 무작위 가게로",
    ),
    "seller.accept_margin": (
        "판매 수락 마진",
        "%",
        "원가+이 마진 이상 제안은 바로 수락",
    ),
    "seller.min_margin": (
        "판매 최소 마진",
        "%",
        "처분 아닌 한 원가+이 마진 아래로 안 팜",
    ),
    "seller.concession": (
        "판매 양보폭",
        "%",
        "역제안마다 호가와 수락선 사이를 이만큼 내려감",
    ),
    "seller.clearance_shelf_ratio": (
        "식품 처분 시작",
        "%",
        "남은 유통기한이 이 비율 아래면 원가 이하로도 처분",
    ),
    "seller.clearance_floor_of_cost": (
        "처분 하한",
        "%",
        "처분 때도 원가의 이 비율 아래로는 안 팜",
    ),
    "seller.stale_markup_cut": (
        "오래된 비식품 할인",
        "%p",
        "오래 안 팔린 비식품 첫 호가를 이만큼 깎음",
    ),
    "seller.stale_goods_hours": (
        "비식품 '오래됨' 기준",
        "시간",
        "이보다 오래 안 팔리면 할인 시작",
    ),
    "seller.markup_a": ("A등급 첫 호가", "%", "정가 대비"),
    "seller.markup_b": ("B등급 첫 호가", "%", "정가 대비"),
    "seller.markup_c": ("C등급 첫 호가", "%", "정가 대비"),
    "seller.warranty_a": ("A등급 보증", "시간", "이 시간 안에 고장 나면 전액 환불"),
    "seller.warranty_b": ("B등급 보증", "시간", "이 시간 안에 고장 나면 전액 환불"),
    "seller.warranty_c": ("C등급 보증", "시간", "이 시간 안에 고장 나면 전액 환불"),
    "restock.replenish_qty": ("재입고 수량", "개", "팔린 SKU를 이만큼 다시 삼"),
    "restock.units_per_sku": ("SKU당 매입 수량", "개", "한 상품을 이만큼씩 쌓아 둠"),
    "restock.min_food_shelf_hours": (
        "식품 매입 최소 유통기한",
        "시간",
        "이보다 짧은 식품은 안 삼",
    ),
    "restock.food_share": ("식품 매입 비중", "%", "매입 금액 중 식품 몫"),
    "restock.categories_per_run": (
        "매입 카테고리 수",
        "개",
        "한 번에 이만큼의 카테고리만 삼",
    ),
    "restock.goods_max_list_price": (
        "비식품 매입 정가 상한",
        "원",
        "이보다 비싼 비식품은 안 삼",
    ),
    "restock.goods_min_list_price": ("비식품 매입 정가 하한", "원", ""),
    "restock.food_max_list_price": (
        "식품 매입 정가 상한",
        "원",
        "이보다 비싼 식품은 안 삼",
    ),
    "restock.food_min_list_price": ("식품 매입 정가 하한", "원", ""),
    "restock.seed_budget": ("시작 매입 예산", "원", ""),
    "restock.reserve_cash": ("예비 현금", "원", "이 아래로는 매입 안 함"),
    "restock.max_units": ("최대 재고 수", "개", ""),
    "restock.stale_hours": (
        "재입고 제외 기준",
        "시간",
        "이보다 오래 안 팔린 SKU는 다시 안 삼",
    ),
}
ACTOR_KO = [
    ("claude-opus", "전략(Opus)"),
    ("claude-sonnet", "전략(Sonnet)"),
    ("claude-haiku", "전술(Haiku)"),
    ("code", "코드 자동"),
    ("user", "사용자"),
    ("claude-code", "Claude Code"),
]
TRIGGER_KO = [
    (re.compile(r"agreed (\d+)/(\d+)"), "바이어 합의율 낮음(1시간 {0}/{1})"),
    (
        re.compile(r"(\d+)/(\d+) sales closed at first ask"),
        "첫 호가에 거의 다 팔림({0}/{1})",
    ),
    (re.compile(r"(\d+) food units near clearance"), "처분 임박 식품 {0}개"),
    (re.compile(r"no sale in 1h after (\d+) in 6h"), "1시간 판매 0건(6시간엔 {0}건)"),
    (
        re.compile(r"Short-shelf food[^.]*?(\d+) settles"),
        "단기 식품이 시장에서 하루 {0}건 팔림",
    ),
    (
        re.compile(r"pinned at band (high|low) for (\d+) tactical runs"),
        "전술이 띠 끝에 {1}번 붙어 띠를 넓힘",
    ),
    (
        re.compile(r"fold recurring tactical moves"),
        "같은 방향 전술 이동 3회 → 기본값으로 고정",
    ),
]


def _actor_ko(actor: str) -> str:
    for prefix, label in ACTOR_KO:
        if actor.startswith(prefix):
            return label
    return actor


def _value_ko(field_key: str, raw: str) -> str:  # noqa: PLR0911 - one unit per branch
    unit = FIELD_KO.get(field_key, ("", "", ""))[1]
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    if isinstance(value, dict) and "low" in value:
        return f"{_value_ko(field_key, json.dumps(value['low']))}~{_value_ko(field_key, json.dumps(value['high']))}"
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, (int, float)):
        if unit == "%":
            return f"{value * 100:.0f}%" if abs(value) <= 1.5 else f"{value:g}"
        if unit == "%p":
            return f"{value * 100:.0f}%p"
        if unit == "원":
            return f"{int(value):,}원"
        return f"{value:g}{unit}"
    return str(value)


def _why_ko(reason: str) -> str:
    parts: list[str] = []
    for pattern, text in TRIGGER_KO:
        found = pattern.search(reason)
        if found:
            parts.append(text.format(*found.groups()))
    return " · ".join(dict.fromkeys(parts))


def change_notice(rows: list[tuple[float, str, str, str, str, str, str]]) -> str:
    """Policy changes since the last pass, in plain Korean: what, from→to, why."""
    lines = ["[AX R3] 정책 변경 (무엇이 왜 바뀌었나)"]
    groups: dict[tuple[int, str], list[tuple[float, str, str, str, str, str, str]]] = {}
    for row in rows:
        groups.setdefault((int(row[0] // 60), row[1]), []).append(row)
    for (_minute, actor), rows_of in groups.items():
        stamp = _kst(rows_of[0][0])
        lines += ["", f"■ {stamp:%H:%M} {_actor_ko(actor)}"]
        for _ts, _actor, _layer, field_key, before, after, _reason in rows_of:
            band = field_key.startswith("band:")
            key = field_key[5:] if band else field_key
            label, _unit, effect = FIELD_KO.get(key, (key, "", ""))
            if band:
                lines.append(
                    f"- {label}의 전술 허용 범위: {_value_ko(key, before)} → {_value_ko(key, after)}"
                )
            else:
                line = f"- {label}: {_value_ko(key, before)} → {_value_ko(key, after)}"
                lines.append(line + (f" ({effect})" if effect else ""))
        why = _why_ko(rows_of[0][6])
        reason = rows_of[0][6].strip()
        if why:
            lines.append(f"근거: {why}")
        elif reason:
            lines.append(f"모델 설명: {reason[:160]}")
    return "\n".join(lines)


def _n(value: object) -> str:
    """Thousands separators for whole numbers; anything else as given."""
    try:
        number = float(str(value))
    except ValueError:
        return str(value)
    return f"{number:,.0f}" if number == int(number) else f"{number:,.2f}"


def summary(snap: Snapshot, title: str) -> str:
    """Short labelled lines that read on a phone; R2-only fields are left out."""
    shop, buyer = snap.shop_row, snap.buyer_row
    r3 = snap.market_round == 3
    lines = [
        f"[AX R3] {title}",
        f"{_kst(snap.now):%m/%d %H:%M} · 라운드 {snap.market_round}",
        "",
    ]
    lines += [
        f"■ 가게 {snap.shop_rank}위",
        f"이익 {_n(shop.get('profit', '?'))} · 매출 {_n(shop.get('revenue', '?'))}",
        f"별점 {shop.get('stars', '?')} · 거래 {_n(shop.get('deals', '?'))}",
        "",
        f"■ 바이어 {snap.buyer_rank}위",
        f"잉여 {_n(buyer.get('saved', '?'))} · 거래 {_n(buyer.get('deals', '?'))}",
    ]
    if r3:
        lines += [
            "",
            "■ 24시간 정산",
            f"가게 {snap.shop_settles_24h} · 바이어 {snap.buyer_settles_24h} · 지급거절 {snap.refused_24h}",
        ]
        if snap.balance is not None:
            lines.append(f"잔고 {_n(snap.balance)} · 재고 {snap.units}개")
    calls = " · ".join(
        f"{ROLE_NAMES.get(role, role)} {int(v[0])}"
        for role, v in sorted(snap.roles_24h.items())
    )
    lines += [
        "",
        "■ 모델",
        f"오늘 ${snap.spend_today:.2f} / 상한 ${snap.daily_cap:.0f}",
        f"24시간 호출: {calls or '없음'}",
        f"관리자 {'켜짐' if snap.manager_on else '꺼짐(전환 전)'} · 종료 {snap.stop_at[5:10].replace('-', '/')}",
    ]
    if snap.loop_runs:
        lines += ["", "■ 관리자 루프 24시간", *loop_counts(snap.loop_runs)]
    if snap.relaunches_24h:
        lines.append(f"자동 복구 {snap.relaunches_24h}회(24시간)")
    if not r3:
        lines += ["", "라운드2라 정산·잔고는 아직 없습니다."]
    return "\n".join(lines)


def decide(
    snap: Snapshot, state: dict[str, object]
) -> tuple[list[str], dict[str, object]]:
    """Return the messages to send now and the updated state."""
    messages: list[str] = []
    new = dict(state)
    sent = dict(new.get("alerts", {})) if isinstance(new.get("alerts"), dict) else {}
    active = conditions(snap)
    if snap.market_round == 3 and "round3_seen_at" not in new:
        new["round3_seen_at"] = snap.now
    seen = float(str(new.get("round3_seen_at", snap.now)))
    if "switch_missing" in active and snap.now - seen < SWITCH_GRACE:
        active.discard("switch_missing")
    for key in sorted(active):
        last = float(sent.get(key, 0))
        if snap.now - last >= REPEAT_SECONDS:
            messages.append(
                summary(
                    snap, "경고: " + TITLES[key] + " Claude 세션에서 확인이 필요합니다."
                )
            )
            sent[key] = snap.now
    for key in sorted(set(sent) - active):
        messages.append(
            f"[AX R3] 회복: {TITLES[key].split('.')[0]} 상태가 해소됐습니다 ({_kst(snap.now):%m/%d %H:%M} KST)."
        )
        del sent[key]
    new["alerts"] = sent
    once = set(new.get("once", [])) if isinstance(new.get("once"), list) else set()
    if snap.market_round == 3 and "round3" not in once:
        messages.append(
            summary(snap, "라운드3가 열렸습니다. 재고 매입·판매·결제가 시작됩니다.")
        )
        once.add("round3")
    if (
        snap.switch_done
        and snap.manager_on
        and snap.stop_at.startswith("2026-11-17")
        and "switch" not in once
    ):
        messages.append(
            summary(snap, "라운드3 전환 완료: 관리자 모델 켜짐, 종료 시각 11/17 23:59.")
        )
        once.add("switch")
    local = _kst(snap.now)
    today = local.date().isoformat()
    if (
        local.hour == SUMMARY_HOUR_KST
        and local.minute >= SUMMARY_FALLBACK_MINUTE
        and new.get("summary_day") != today
    ):
        messages.append(summary(snap, "아침 요약"))
        new["summary_day"] = today
    if snap.now >= ROUND_END.timestamp() and "final" not in once:
        messages.append(
            summary(snap, "라운드3 종료. 이 수치로 보고서 마무리가 필요합니다.")
        )
        once.add("final")
    new["once"] = sorted(once)
    return messages + loop_messages(snap, new), new


def loop_messages(snap: Snapshot, state: dict[str, object]) -> list[str]:
    """Policy changes since the last pass, one loop digest per hour, new notes."""
    messages: list[str] = []
    notes_cursor = float(str(state.get("notes_cursor", snap.now - 600)))
    new_notes = [n for n in snap.engineer_notes if n[0] > notes_cursor]
    if new_notes:
        messages.append(notes_message(new_notes))
        state["notes_cursor"] = max(n[0] for n in new_notes)
    if not snap.loop_runs:
        return messages
    cursor = float(str(state.get("loop_cursor", snap.now - 600)))
    fresh = [r for r in snap.policy_changes if r[0] > cursor]
    if fresh:
        messages.append(change_notice(fresh))
    state["loop_cursor"] = max(
        [cursor, snap.loop_runs[-1][0]] + [r[0] for r in snap.policy_changes]
    )
    hour = f"{_kst(snap.now):%Y-%m-%d %H}"
    recent = any(snap.now - r[0] <= 3600 for r in snap.loop_runs)
    if state.get("digest_hour") != hour and recent:
        if "digest_hour" in state:  # the first pass only sets the clock
            messages.append(loop_digest(snap))
        state["digest_hour"] = hour
    return messages


def notes_message(notes: list[tuple[float, str]]) -> str:
    """Engineer notes the strategy model filed: for Claude to turn into PRs."""
    lines = ["[AX R3] 엔지니어 메모 (전략 모델 → Claude 작업 후보)"]
    lines += [f"{_kst(ts):%m/%d %H:%M} · {note[:280]}" for ts, note in notes[-5:]]
    lines.append("파일: data/r3/r3-engineer-notes.jsonl")
    return "\n".join(lines)


def send(text: str, *, dry_run: bool) -> bool:
    """Hand the text to the Hermes gateway (Telegram home channel)."""
    if dry_run:
        print(text)
        print("---")
        return True
    result = subprocess.run(
        [
            "docker",
            "exec",
            "-u",
            "10000",
            "-e",
            "HOME=/opt/data",
            HERMES,
            "/opt/hermes/.venv/bin/hermes",
            "send",
            "-t",
            "telegram",
            "-q",
            text,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return result.returncode == 0


def vault_job(
    heading: str, body: str, key: str, *, dry_run: bool, jobs: Path = JOBS
) -> Path | None:
    """Queue one append for the Mac worker; the same key is applied only once."""
    if not body.strip():
        return None
    now = datetime.now(UTC).astimezone(KST)
    safe = "".join(ch if ch.isalnum() else "_" for ch in key)[-60:]
    name = f"job_{now:%Y%m%d_%H%M%S}_KST_MAC_WORKER_APPROVED_OBSIDIAN_AX_R3_{safe}.md"
    content = "\n".join("  " + line for line in body.splitlines())
    text = (
        "---\n"
        "type: obsidian_update_request\n"
        "status: pending\n"
        "approval_source: user_request_2026-11-02_ax_r3_advisor_vault_log\n"
        "vault_write_by_hermes: false\n"
        "worker_route: mac_local_worker\n"
        "approved_worker_tag: MAC_WORKER_APPROVED_OBSIDIAN\n"
        "---\n\n"
        "## jarvis_apply\n"
        "```yaml\n"
        "version: 1\n"
        "operation: append_after_heading\n"
        f'target: "{VAULT_NOTE}"\n'
        f'heading: "{heading}"\n'
        "content: |\n"
        f"{content}\n"
        f'idempotency_key: "{key}"\n'
        "```\n\n"
        "## evidence\n"
        "- AX R3 VPS watcher (notify_r3.py), source /docker/ax-week08-25620027/data/r3/\n"
    )
    path = jobs / name
    if dry_run:
        print(text)
        return path
    path.write_text(text, encoding="utf-8")
    return path


def as_markdown(message: str, stamp: str) -> str:
    """Turn a phone message into a dated Markdown block for the vault note."""
    lines = [line for line in message.splitlines() if line.strip()]
    if not lines:
        return ""
    head = lines[0].replace("[AX R3] ", "")
    out = [f"### {stamp} {head} (Claude Code 자동 기록)"]
    for line in lines[1:]:
        if line.startswith("■ "):
            out.append(f"**{line[2:]}**")
        elif line.startswith("#"):
            out.append(f"- **{line}**")
        else:
            out.append(f"  - {line}")
    return "\n".join(out) + "\n"


def ack_advice(ids: list[int], *, dry_run: bool) -> bool:
    """Acknowledge delivered ids; this does not approve or execute a proposal."""
    if dry_run:
        return False
    if not ids or any(type(value) is not int or value <= 0 for value in ids):
        return False
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                CONTAINER,
                "python",
                "-m",
                "r3.advisor",
                "--dir",
                "/data/r3",
                "--ack",
                ",".join(map(str, ids)),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def advice_outbox(state_path: Path) -> Path:
    """Durable delivery queue beside watcher state, with no credentials."""
    return state_path.with_name("advisor-outbox.json")


def queue_advice(state_path: Path, message: str, ids: object, *, dry_run: bool) -> None:
    """Store pending delivery before attempting Telegram; never mark it sent here."""
    if dry_run or not message or not isinstance(ids, list) or not ids:
        return
    if any(type(value) is not int or value <= 0 for value in ids):
        return
    target = advice_outbox(state_path)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"message": message, "ids": ids}, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(target)


def retry_advice(state_path: Path, *, dry_run: bool) -> bool:
    """Retry the same approval request without another model call.

    A crash between send and acknowledgement can repeat the same proposal id;
    at-least-once delivery is preferable to silently losing a request.
    """
    target = advice_outbox(state_path)
    try:
        pending = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(pending, dict):
        return False
    message, ids = pending.get("message"), pending.get("ids")
    if not isinstance(message, str) or not isinstance(ids, list) or not ids:
        return False
    if any(type(value) is not int or value <= 0 for value in ids):
        return False
    if not send(message, dry_run=dry_run) or not ack_advice(ids, dry_run=dry_run):
        return False
    target.unlink(missing_ok=True)
    return True


def advisor(slot: str, state_path: Path, *, dry_run: bool, force: bool = False) -> str:  # noqa: C901 - one scheduled pass
    """Run the advisor inside the trading container and send what it reports.

    The 09 KST run always sends (morning summary plus any proposals) and marks the
    day's summary as sent; other runs send only when there are new proposals.
    """
    if dry_run:
        return f"advisor slot={slot} dry-run: no model request or state changes"
    # Before Round 3 the market has no payments and no stock, and the advisor reads
    # that as an emergency (2026-11-02 21:45: two false "urgent" proposals). Scheduled
    # runs skip until the board says round 3; a manual run passes --force.
    if not force:
        try:
            with urlopen(f"{MARKET}/api/board", timeout=20) as reply:  # noqa: S310
                market_round = int(json.loads(reply.read()).get("round") or 0)
        except (OSError, ValueError, TypeError):
            market_round = 0
        if market_round != 3:
            return f"advisor slot={slot} skipped: market round {market_round}"
    try:
        runtime = json.loads(
            (BASE / "private/runtime.json").read_text(encoding="utf-8")
        )
        shop_url = str(runtime.get("public_url", ""))
    except (OSError, ValueError):
        shop_url = ""
    result = subprocess.run(
        [
            "docker",
            "exec",
            # the advisor only writes proposals, so it may run before the switch
            # turns the policy loops on; this opens the model gate for this process only
            "-e",
            "AX_R3_MANAGER_MODEL=1",
            CONTAINER,
            "python",
            "-m",
            "r3.advisor",
            "--dir",
            "/data/r3",
            "--shop-url",
            shop_url,
            "--slot",
            slot,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )
    try:
        report = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        report = {"message": "", "skipped": f"advisor exit {result.returncode}"}
    message = str(report.get("message", ""))
    ids = report.get("proposal_ids", [])
    queue_advice(state_path, message, ids, dry_run=dry_run)
    if message:
        day = _kst(time.time()).date().isoformat()
        _ = vault_job(
            "## 자문 보고",
            as_markdown(message, f"{day} {slot}시"),
            f"ax-r3-advisor-{day}-{slot}",
            dry_run=dry_run,
        )
    delivered = False
    if slot == f"{SUMMARY_HOUR_KST:02d}":
        snap = collect()
        body = summary(snap, "아침 요약") + ("\n\n" + message if message else "")
        delivered = send(body, dry_run=dry_run)
        if delivered and not dry_run:
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                state = {}
            state["summary_day"] = _kst(snap.now).date().isoformat()
            state_path.write_text(
                json.dumps(state, ensure_ascii=False), encoding="utf-8"
            )
    elif message:
        delivered = send(message, dry_run=dry_run)
    if delivered and isinstance(ids, list) and ack_advice(ids, dry_run=dry_run):
        advice_outbox(state_path).unlink(missing_ok=True)
    return f"advisor slot={slot} queued={ids} delivered={delivered} skipped={report.get('skipped', '')}"


def main() -> None:
    """CLI: --state FILE [--dry-run] [--test MESSAGE] [--summary] [--advisor HH]."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--state", type=Path, default=BASE / "notify-state.json")
    _ = parser.add_argument("--dry-run", action="store_true")
    _ = parser.add_argument("--test", default="")
    _ = parser.add_argument("--summary", action="store_true")
    _ = parser.add_argument("--advisor", default="")
    _ = parser.add_argument("--force", action="store_true")
    _ = parser.add_argument("--vault-change", default="")
    args = parser.parse_args()
    if time.time() >= STOP_SENDING.timestamp() and not (args.test or args.summary):
        return
    if args.test:
        ok = send(args.test, dry_run=args.dry_run)
        print("sent" if ok else "send failed")
        return
    if args.vault_change:
        day = _kst(time.time())
        key = f"ax-r3-change-{day:%Y%m%d-%H%M%S}"
        path = vault_job(
            "## 반영 기록",
            f"- {day:%Y-%m-%d %H:%M} {args.vault_change} (Claude Code)",
            key,
            dry_run=args.dry_run,
        )
        print(f"queued {path}")
        return
    if args.advisor:
        print(advisor(args.advisor, args.state, dry_run=args.dry_run, force=args.force))
        return
    snap = collect()
    if args.summary:
        ok = send(summary(snap, "현재 상태"), dry_run=args.dry_run)
        print("sent" if ok else "send failed")
        return
    _ = retry_advice(args.state, dry_run=args.dry_run)
    try:
        state = json.loads(args.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    messages, new_state = decide(snap, state if isinstance(state, dict) else {})
    delivered = all(send(m, dry_run=args.dry_run) for m in messages)
    if delivered and not args.dry_run:
        args.state.write_text(
            json.dumps(new_state, ensure_ascii=False), encoding="utf-8"
        )
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    print(
        f"{stamp} active={sorted(conditions(snap))} sent={len(messages)} ok={delivered}"
    )


if __name__ == "__main__":
    main()
