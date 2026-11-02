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
BUYER = "25620027"
SWITCH_AT = datetime(
    2026, 11, 2, 15, 12, tzinfo=UTC
)  # 00:12 KST, after the 00:05 switch
ROUND_END = datetime(2026, 11, 17, 14, 59, 59, tzinfo=UTC)
STOP_SENDING = ROUND_END + timedelta(hours=1)
REPEAT_SECONDS = 7200.0
KIT_STALE_SECONDS = 900.0
SUMMARY_HOUR_KST = 9
TITLES = {
    "container_down": "거래 컨테이너가 내려가 있습니다 (자동 복구가 3분마다 시도 중).",
    "kit_stale": "키트 로그가 15분 넘게 멈췄습니다. 거래가 멈췄을 수 있습니다.",
    "relaunch_loop": "지난 1시간에 자동 재기동이 3회 이상입니다. 재기동이 반복 실패하는 중일 수 있습니다.",
    "switch_missing": "00:05 라운드3 전환이 확인되지 않습니다. 이대로면 11/3 23:59에 거래가 멈춥니다.",
    "billing": "모델 API가 결제·크레딧 거절을 돌려 30분 휴지 중입니다. 거래는 규칙으로 계속됩니다.",
    "spend_cap": "오늘 모델 비용이 일일 상한에 닿았습니다. 전략이 Haiku로 강등됩니다.",
    "no_sales": "라운드3인데 지난 1시간 우리 가게·바이어 정산이 0건입니다(시장은 거래 중).",
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
    if snap.now >= SWITCH_AT.timestamp() and not (
        snap.manager_on and snap.stop_at.startswith("2026-11-17") and snap.switch_done
    ):
        active.add("switch_missing")
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
    return active


ROLE_NAMES = {"scout": "정찰", "tactical": "전술", "strategy": "전략", "trade": "통역"}


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
            summary(snap, "00:05 전환 완료: 관리자 모델 켜짐, 종료 시각 11/17 23:59.")
        )
        once.add("switch")
    local = _kst(snap.now)
    today = local.date().isoformat()
    if local.hour == SUMMARY_HOUR_KST and new.get("summary_day") != today:
        messages.append(summary(snap, "아침 요약"))
        new["summary_day"] = today
    if snap.now >= ROUND_END.timestamp() and "final" not in once:
        messages.append(
            summary(snap, "라운드3 종료. 이 수치로 보고서 마무리가 필요합니다.")
        )
        once.add("final")
    new["once"] = sorted(once)
    return messages, new


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


def main() -> None:
    """CLI: --state FILE [--dry-run] [--test MESSAGE] [--summary]."""
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("--state", type=Path, default=BASE / "notify-state.json")
    _ = parser.add_argument("--dry-run", action="store_true")
    _ = parser.add_argument("--test", default="")
    _ = parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    if time.time() >= STOP_SENDING.timestamp() and not (args.test or args.summary):
        return
    if args.test:
        ok = send(args.test, dry_run=args.dry_run)
        print("sent" if ok else "send failed")
        return
    snap = collect()
    if args.summary:
        ok = send(summary(snap, "현재 상태"), dry_run=args.dry_run)
        print("sent" if ok else "send failed")
        return
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
