"""Turn model calls off before or when the account runs out; everything falls back to coded rules.

Usage comes from the local Codex app-server's account read, which runs no model turn. It is read only when a model call
is about to happen and at most once per read_interval_seconds. Usage that is old or unreadable counts as off. A usage
limit error opens the breaker until the exhausted window resets, or for a configured time when that is unknown.
"""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parent
DEFAULTS = {"enabled": True, "five_hour_limit_percent": 85, "weekly_limit_percent": 85, "read_interval_seconds": 300,
            "max_usage_age_seconds": 1800, "limit_error_off_seconds": 3600, "read_timeout_seconds": 15}
LIMIT_WORDS = re.compile(r"usage limit|rate.?limit|too many requests|\b429\b|quota|limit reached|exceeded your|insufficient_quota", re.I)


def load_config(root=ROOT):
    """A value that cannot be used falls back to the protective default; only an explicit false disables the breaker."""
    try:
        loaded = json.loads((Path(root) / "model_breaker.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = {}
    settings = dict(DEFAULTS)
    if isinstance(loaded, dict):
        if loaded.get("enabled") is False:
            settings["enabled"] = False
        for key in DEFAULTS:
            value = loaded.get(key)
            if key != "enabled" and type(value) in (int, float) and math.isfinite(value) and value > 0:
                settings[key] = value
    return settings


def _state_path(root):
    return Path(root) / ".market/model-breaker.json"


def read_state(root=ROOT):
    """Return the state, or {} for a missing or damaged file. Fresh usage rebuilds it."""
    try:
        state = json.loads(_state_path(root).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(root, state):
    path = _state_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".model-breaker.", suffix=".tmp")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream)
        os.replace(temporary, path)
    except OSError:
        pass  # A lost update only means reading usage again.


def read_usage(timeout=DEFAULTS["read_timeout_seconds"], command=None):
    """Ask the app-server for rate limits; keep only percentages and reset times. Returns None when it cannot."""
    if command is None:
        from harness import build_command
        command = [build_command("codex")[0], "app-server"]
    process = None
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   text=True, encoding="utf-8")
        found = []

        def collect():
            for line in process.stdout:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict) and row.get("id") == 2:
                    found.append(row)
                    return
        reader = threading.Thread(target=collect, daemon=True)
        reader.start()
        for message in ({"method": "initialize", "id": 1, "params": {"clientInfo": {"name": "usage-read", "title": None, "version": "0"}}},
                        {"method": "initialized"}, {"method": "account/rateLimits/read", "id": 2}):
            process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()
        reader.join(timeout)
        return parse_usage(found[0]) if found else None
    except (OSError, ValueError, TypeError):
        return None
    finally:
        if process is not None:
            try:
                process.kill()
                process.wait(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass


def parse_usage(answer):
    limits = ((answer or {}).get("result") or {}).get("rateLimits")
    if not isinstance(limits, dict):
        return None
    usage = {}
    for name, label in (("primary", "five_hour"), ("secondary", "weekly")):
        window = limits.get(name)
        percent = window.get("usedPercent") if isinstance(window, dict) else None
        if type(percent) not in (int, float) or not math.isfinite(percent):
            return None
        usage[f"{label}_percent"] = percent
        resets = window.get("resetsAt")
        usage[f"{label}_resets_at"] = resets if type(resets) in (int, float) else None
    return usage


def _announce(root, role, state, allowed, reason, now, config=None):
    """Say so once per transition, in the terminal and a log, with no account data beyond percentages."""
    if state.get("model_on") == allowed and state.get("reason") == reason:
        return False
    usage = state.get("usage") if isinstance(state.get("usage"), dict) else {}
    state.update(model_on=allowed, reason=reason, changed_at=now)
    line = {"at": datetime.fromtimestamp(now, timezone.utc).isoformat(), "role": role, "model": "on" if allowed else "off", "reason": reason,
            "five_hour_percent": usage.get("five_hour_percent"), "weekly_percent": usage.get("weekly_percent")}
    print(f"[model] {role}: model {line['model']} ({reason}; 5h {line['five_hour_percent']}%, weekly {line['weekly_percent']}%)", flush=True)
    try:
        path = Path(root) / "logs/model-breaker.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(line) + "\n")
    except OSError:
        pass
    return True


def check(root=ROOT, role="buyer", now=None, reader=None):
    """Return (allowed, reason). Never raises: any failure here means the caller uses its rules."""
    now = time.time() if now is None else now
    try:
        config = load_config(root)
        if not config["enabled"]:
            return True, "breaker_disabled"
        state = read_state(root)
        changed = False
        until = state.get("tripped_until")
        if type(until) in (int, float) and until > now:
            allowed, reason = False, "usage_limit_error"
        else:
            if until is not None:
                state.pop("tripped_until", None)
                changed = True
            usage = state.get("usage") if isinstance(state.get("usage"), dict) else None
            read_at = usage.get("read_at") if usage else None
            if type(read_at) not in (int, float) or now - read_at >= config["read_interval_seconds"]:
                fresh = (reader or read_usage)(config["read_timeout_seconds"])
                if fresh:
                    usage = dict(fresh, read_at=now)
                    state["usage"] = usage
                    changed = True
            read_at = usage.get("read_at") if usage else None
            if type(read_at) not in (int, float):
                allowed, reason = False, "usage_unreadable"
            elif now - read_at > config["max_usage_age_seconds"]:
                allowed, reason = False, "usage_stale"
            elif not type(usage.get("five_hour_percent")) in (int, float) or not type(usage.get("weekly_percent")) in (int, float):
                allowed, reason = False, "usage_unreadable"
            elif usage["five_hour_percent"] >= config["five_hour_limit_percent"]:
                allowed, reason = False, "five_hour_usage_high"
            elif usage["weekly_percent"] >= config["weekly_limit_percent"]:
                allowed, reason = False, "weekly_usage_high"
            else:
                allowed, reason = True, "ok"
        changed = _announce(root, role, state, allowed, reason, now) or changed
        if changed:
            _write_state(root, state)
        return allowed, reason
    except Exception as error:
        print(f"[model] {role}: model off (breaker_error: {type(error).__name__})", flush=True)
        return False, "breaker_error"


def is_tripped(root=ROOT, now=None):
    now = time.time() if now is None else now
    try:
        if not load_config(root)["enabled"]:
            return False
        until = read_state(root).get("tripped_until")
        return type(until) in (int, float) and until > now
    except Exception:
        return False


def trip(root=ROOT, role="harness", now=None, reader=None):
    """A usage limit error was seen. Stay off until the exhausted window resets, or for the configured time."""
    now = time.time() if now is None else now
    try:
        config = load_config(root)
        state = read_state(root)
        until = now + config["limit_error_off_seconds"]
        fresh = (reader or read_usage)(config["read_timeout_seconds"])
        if fresh:
            state["usage"] = dict(fresh, read_at=now)
            exhausted = [fresh[f"{label}_resets_at"] for label in ("five_hour", "weekly")
                         if fresh[f"{label}_percent"] >= 100 and fresh[f"{label}_resets_at"] and fresh[f"{label}_resets_at"] > now]
            if exhausted:
                until = max(exhausted)
        state["tripped_until"] = until
        _announce(root, role, state, False, "usage_limit_error", now)
        _write_state(root, state)
    except Exception as error:
        print(f"[model] {role}: could not record the limit error ({type(error).__name__})", flush=True)


def looks_like_limit_error(text):
    return bool(LIMIT_WORDS.search(text or ""))
