"""One strategist run per hour: collect, brief, ask, guard, rewrite SKILL.md, record.

The shop reads strategy/SKILL.md on every call, so the only thing this process changes in the
shop is a few numbers in that file. Everything else it does is for the user to check later:
each run leaves its brief, prompt, raw model answer and a Markdown report, one runs.jsonl row,
and one changes.jsonl row per rewrite. A run never raises and never retries a write: a SKILL.md
that is invalid, paused (strategy/STRATEGIST_PAUSE) or edited during the run is left as it is,
and the reason is recorded. One process at a time holds home/lock. Two strategists with different
homes may still point at the same shop, so what protects SKILL.md lives next to it: the rewrite
holds strategy/.strategist.lock, and strategy/.strategist-cooldown.json (written right after each
rewrite) carries the cooldown to every strategist and across a crash.

Without an explicit shop (--shop or SHOP_HOME) the shop is this repository, whose strategy/SKILL.md
is the one the shop image ships, so the default mode is then propose and nothing is written.
"""
import fcntl
import json
import math
import os
import signal
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

import httpx

from memory.store import append_jsonl, atomic_write_json, read_json, tail_jsonl
from strategist import analyze, collect, decide, guard, skillfile

W = Path(__file__).resolve().parents[1]
HEALTH_MAX_AGE_S = 300
HEARTBEAT_S = 60
MAX_HISTORY = 6


@dataclass
class Config:
    shop_home: Path
    home: Path
    mode: str = "apply"                 # "apply" | "propose" (never writes SKILL.md)
    interval_s: int = 3600
    first_delay_s: int = 60
    market_url: str = "https://market.ourolabs.ai"
    min_confidence: float = 0.6
    max_changes: int = 3
    cooldown_h: float = 3
    llm_timeout: int = 300
    me: str = "psyoung82"

    @property
    def skill(self):
        return self.shop_home / "strategy" / "SKILL.md"

    @property
    def pause_file(self):
        return self.shop_home / "strategy" / "STRATEGIST_PAUSE"

    @property
    def skill_lock(self):
        return self.shop_home / "strategy" / ".strategist.lock"

    @property
    def cooldown_file(self):
        return self.shop_home / "strategy" / ".strategist-cooldown.json"


def config(shop=None, home=None, mode=None, interval=None):
    """Config from the environment; arguments that are not None win. The mode defaults to apply only
    for an explicit shop: the repository's own SKILL.md is never rewritten unless asked with --mode apply."""
    shop = shop or os.environ.get("SHOP_HOME")
    mode = mode or os.environ.get("STRATEGIST_MODE") or ("apply" if shop else "propose")
    if mode not in ("apply", "propose"):
        raise ValueError(f"STRATEGIST_MODE must be apply or propose, not {mode!r}")
    return Config(shop_home=Path(shop or W),
                  home=Path(home or os.environ.get("STRATEGIST_HOME", W / ".market" / "strategist")),
                  mode=mode,
                  interval_s=int(interval or os.environ.get("STRATEGIST_INTERVAL_S", 3600)),
                  market_url=os.environ.get("MARKET_URL", "https://market.ourolabs.ai"))


# --- small file helpers

def _lock(home):
    """An open file holding the exclusive lock home/lock, or None when another process has it."""
    return _flock(home / "lock")


def _flock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    f = open(path, "a")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def _write_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _run_id(home, now):
    """YYYYmmdd-HHMM local time; a second run in the same minute gets -2, -3, ..."""
    base = time.strftime("%Y%m%d-%H%M", time.localtime(now))
    rid, n = base, 1
    while (home / "briefs" / f"{rid}.json").exists():
        n += 1
        rid = f"{base}-{n}"
    return rid


def _history(home):
    """The last runs as the prompt shows them: what was changed and our metrics then."""
    rows = [r for r in tail_jsonl(home / "runs.jsonl", MAX_HISTORY) if isinstance(r, dict)]
    return [{"run_id": r.get("run_id"), "ts": r.get("ts"), "status": r.get("status"),
             "applied": [{k: a.get(k) for k in ("key", "old", "new")} for a in r.get("applied") or []],
             "confidence": r.get("confidence"), "me": r.get("me")} for r in rows]


# --- the run

def run_once(cfg, now=None, client=None):
    """One run under the lock. Returns the runs.jsonl row; never raises."""
    now = time.time() if now is None else now
    lock = _lock(cfg.home)
    if lock is None:
        return {"ts": now, "status": "locked", "errors": ["another strategist holds the lock"]}
    try:
        return _run(cfg, now, client)
    finally:
        lock.close()


def _run(cfg, now, client=None):
    t0 = time.monotonic()
    home = cfg.home
    paused = cfg.pause_file.exists()
    rec = {"run_id": _run_id(home, now), "ts": now, "mode": cfg.mode, "paused": paused, "status": None,
           "policy_ver_before": None, "policy_ver_after": None, "applied": [], "proposed": [], "rejected": [],
           "confidence": None, "assessment": "", "watch": [], "errors": [], "me": None, "elapsed_ms": None}
    state = read_json(home / "state.json", {})
    state = state if isinstance(state, dict) else {}
    try:
        _steps(cfg, now, client, state, rec)
    except Exception as e:                              # record and keep the process alive
        rec["status"] = "crash"
        rec["errors"].append(f"crash: {type(e).__name__}: {str(e)[:300]}")
        rec["errors"].append(traceback.format_exc(limit=3)[-600:])
    rec["elapsed_ms"] = round((time.monotonic() - t0) * 1000)
    try:
        append_jsonl(home / "runs.jsonl", rec)
        state["heartbeat"] = time.time()
        state["last_run"] = rec["run_id"]
        atomic_write_json(home / "state.json", state)
    except Exception as e:
        rec["errors"].append(f"record: {type(e).__name__}: {e}")
    return rec


def _steps(cfg, now, client, state, rec):
    home, rid = cfg.home, rec["run_id"]
    # 1 the file exactly as it is on disk (not policy.load, which may fall back silently)
    text, front, body, skill_errors = skillfile.read(cfg.skill)
    pol = front.get("policy") if isinstance(front.get("policy"), dict) else {}
    if skill_errors:
        rec["errors"].append("skill invalid: " + "; ".join(skill_errors)[:500])
    else:
        rec["policy_ver_before"] = rec["policy_ver_after"] = skillfile.ver(text)
        _check_version(front.get("version"), state, rec)

    # 2 collect and brief
    own_client = client is None
    client = client or httpx.Client(base_url=cfg.market_url, timeout=20)
    try:
        raw, errors = collect.fetch_public(client, state, now, cache_dir=home / "cache", me=cfg.me)
    finally:
        if own_client:
            client.close()
    window_h = max(1, math.ceil(cfg.interval_s / 3600))
    own = collect.read_own(cfg.shop_home, now, window_h=window_h)
    if own.get("error"):
        errors.append(own["error"])
        raw["errors"] = errors
    rec["errors"] += errors
    brief = analyze.brief(raw, own, state.get("last_brief"), pol, now, me=cfg.me, window_h=window_h)
    rec["me"] = brief.get("me")
    atomic_write_json(home / "briefs" / f"{rid}.json", brief)
    state["last_brief"] = brief
    if skill_errors:
        rec["status"] = "skill invalid"
        _report(cfg, rec, brief)
        return

    # 3-4 ask the model
    prompt = decide.build_prompt(body, pol, guard.tunable_view(pol), brief, _history(home))
    _write_text(home / "prompts" / f"{rid}.txt", prompt)
    obj, raw_answer, err = decide.ask(prompt, cfg.llm_timeout)
    _write_text(home / "raw" / f"{rid}.txt", raw_answer or "")
    if err:
        rec["status"] = "llm error"
        rec["errors"].append(f"llm: {err}")
        _report(cfg, rec, brief)
        return
    rec.update(confidence=obj["confidence"], assessment=obj["assessment"], watch=obj["watch"])
    if obj["confidence"] < cfg.min_confidence:
        rec["status"] = "low confidence"
        why = f"confidence {obj['confidence']} < {cfg.min_confidence}"
        rec["rejected"] = [{"key": c.get("key"), "requested": c.get("value"), "why": why} for c in obj["changes"]]
        _report(cfg, rec, brief)
        return

    # 5 guard, then write. The cooldown is this home's and every other strategist's on this shop.
    last_ts = state.setdefault("last_change_ts", {})
    shared = read_json(cfg.cooldown_file, {})
    for k, t in (shared.items() if isinstance(shared, dict) else ()):
        if isinstance(t, (int, float)) and not isinstance(t, bool) and t > (last_ts.get(k) or 0):
            last_ts[k] = t
    accepted, rejected = guard.guard(obj["changes"], pol, last_ts, now, cfg.max_changes, cfg.cooldown_h)
    rec["rejected"] = rejected
    rec["paused"] = rec["paused"] or cfg.pause_file.exists()      # created during the model call
    if not accepted:
        rec["status"] = "no change"
    elif cfg.mode != "apply" or rec["paused"]:
        rec["status"] = "paused" if rec["paused"] else "proposed"
        rec["proposed"] = accepted
    else:
        _apply(cfg, now, text, accepted, last_ts, rec)
        if rec["applied"] and isinstance(state.get("skill_version"), int):
            state["skill_version"] += 1                 # skillfile.apply bumps version by one
    _report(cfg, rec, brief)


def _check_version(version, state, rec):
    """A version lower than the last one seen means the file was replaced (a new shop image stages its
    own SKILL.md): the strategist's earlier changes are gone. Recorded, not undone."""
    last = state.get("skill_version")
    if isinstance(version, int) and isinstance(last, int) and version < last:
        rec["errors"].append(f"SKILL.md version went back from {last} to {version}: the file was replaced "
                             "(new shop image?), so earlier strategist changes are no longer in it")
    if isinstance(version, int):
        state["skill_version"] = version


def _apply(cfg, now, text, accepted, last_ts, rec):
    lock = None
    try:
        lock = _flock(cfg.skill_lock)
        if lock is None:
            rec["status"], rec["proposed"] = "locked", accepted
            rec["errors"].append(f"another strategist is writing {cfg.skill.name}; nothing written")
            return
        new_text = skillfile.apply(cfg.skill, text, {a["key"]: a["new"] for a in accepted})
    except skillfile.Conflict as e:
        rec["status"], rec["proposed"] = "conflict", accepted
        rec["errors"].append(f"conflict: {e}; nothing written")
        return
    except (ValueError, OSError) as e:
        rec["status"], rec["proposed"] = "apply failed", accepted
        rec["errors"].append(f"apply: {type(e).__name__}: {str(e)[:300]}")
        return
    finally:
        if lock is not None:
            lock.close()
    rec["status"], rec["applied"] = "applied", accepted
    rec["policy_ver_after"] = skillfile.ver(new_text)
    for a in accepted:
        last_ts[a["key"]] = now
    try:                                                # first, so a crash right after keeps the cooldown
        shared = read_json(cfg.cooldown_file, {})
        shared = shared if isinstance(shared, dict) else {}
        atomic_write_json(cfg.cooldown_file, shared | {a["key"]: now for a in accepted})
    except OSError as e:
        rec["errors"].append(f"cooldown file: {type(e).__name__}: {e}")
    append_jsonl(cfg.home / "changes.jsonl", {
        "ts": now, "old_ver": rec["policy_ver_before"], "new_ver": rec["policy_ver_after"],
        "diff": {a["key"]: [a["old"], a["new"]] for a in accepted},
        "reason": "; ".join(f"{a['key']}: {a['reason']}" for a in accepted),
        "source": "strategist", "run_id": rec["run_id"]})


# --- 6 the report the user reads

def _cell(v):
    return "-" if v is None else str(v).replace("|", "\\|").replace("\n", " ")


def _metrics(brief):
    lines = []
    delta = brief.get("delta") or {}
    for sec in ("shop", "buyer"):
        d = (brief.get("me") or {}).get(sec)
        if not d:
            continue
        lines += [f"\n**{sec}**\n", "| 지표 | 값 | 지난 실행 대비 |", "|---|---|---|"]
        dd = delta.get(sec) or {}
        lines += [f"| {k} | {_cell(v)} | {_cell(dd.get(k, ''))} |" for k, v in d.items()]
    return lines or ["(보드에서 우리 행을 찾지 못함)"]


def _report(cfg, rec, brief):
    when = time.strftime("%Y-%m-%d %H:%M %Z", time.localtime(rec["ts"]))
    mode = rec["mode"] + (" (일시정지: STRATEGIST_PAUSE)" if rec["paused"] else "")
    out = [f"# 전략 점검 {rec['run_id']}", "",
           f"- 시각: {when}", f"- 모드: {mode}", f"- 결과: **{rec['status']}**",
           f"- 정책 버전: {rec['policy_ver_before']} → {rec['policy_ver_after']}",
           f"- 확신도: {rec['confidence']}", f"- 남은 시간: {brief.get('hours_left')} h", "",
           "## 판단", "", rec["assessment"] or "(없음)", "", "## 우리 지표"]
    out += _metrics(brief)
    out += ["", "## 적용한 변경", ""]
    out += [f"- `{a['key']}`: {a['old']} → {a['new']}" + (f" (요청 {a['requested']}, 범위로 조정)" if a["clamped"] else "")
            + f" — {_cell(a['reason'])}" for a in rec["applied"]] or ["(없음)"]
    if rec["proposed"]:
        out += ["", "## 통과했지만 쓰지 않은 변경", ""]
        out += [f"- `{a['key']}`: {a['old']} → {a['new']} — {_cell(a['reason'])}" for a in rec["proposed"]]
    out += ["", "## 거절한 제안", ""]
    out += [f"- `{_cell(r['key'])}` = {_cell(r['requested'])}: {_cell(r['why'])}" for r in rec["rejected"]] or ["(없음)"]
    out += ["", "## 다음에 볼 것", ""] + ([f"- {_cell(w)}" for w in rec["watch"]] or ["(없음)"])
    out += ["", "## 오류", ""] + ([f"- {_cell(e)}" for e in rec["errors"]] or ["(없음)"])
    text = "\n".join(out) + "\n"
    _write_text(cfg.home / "reports" / f"{rec['run_id']}.md", text)
    _write_text(cfg.home / "reports" / "latest.md", text)


# --- long-running process and health

def _beat(home):
    try:
        atomic_write_json(home / "heartbeat.json", {"ts": time.time(), "pid": os.getpid()})
    except OSError:
        pass


def summary(rec):
    """The one-line JSON a run prints."""
    return json.dumps({k: rec.get(k) for k in ("run_id", "status", "mode", "paused", "policy_ver_after",
                                                "confidence", "elapsed_ms")}
                      | {"applied": [f"{a['key']} {a['old']}->{a['new']}" for a in rec.get("applied") or []],
                         "rejected": len(rec.get("rejected") or []), "errors": len(rec.get("errors") or [])},
                      ensure_ascii=False)


def loop(cfg):
    """Run every interval_s from run start, the first one first_delay_s after start. Exit 0 on SIGTERM/SIGINT.
    The heartbeat is its own file, written by a thread, so a long run (slow market, 300 s model call)
    still looks alive to the healthcheck and never races the run's state.json write."""
    lock = _lock(cfg.home)
    if lock is None:
        print(json.dumps({"status": "locked", "error": f"another strategist holds {cfg.home / 'lock'}"}), flush=True)
        return 1
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    _beat(cfg.home)

    def beats():
        while not stop.wait(HEARTBEAT_S):
            _beat(cfg.home)

    threading.Thread(target=beats, daemon=True).start()
    next_run = time.time() + cfg.first_delay_s
    try:
        while not stop.is_set():
            if time.time() >= next_run:
                start = time.time()
                print(summary(_run(cfg, start)), flush=True)
                next_run = start + cfg.interval_s
            else:
                stop.wait(1)
    finally:
        lock.close()
    return 0


def health(home, now=None):
    """0 when a heartbeat is younger than HEALTH_MAX_AGE_S, else 1."""
    now = time.time() if now is None else now
    ts = []
    for name, key in (("heartbeat.json", "ts"), ("state.json", "heartbeat")):
        d = read_json(Path(home) / name, {})
        t = d.get(key) if isinstance(d, dict) else None
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            ts.append(t)
    return 0 if ts and now - max(ts) < HEALTH_MAX_AGE_S else 1
