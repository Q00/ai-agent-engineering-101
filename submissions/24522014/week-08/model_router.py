"""Model order: agy, then codex, then the caller's coded rules. Agy calls run with no way to act on the machine.

agy is a tool-using agent and these prompts carry seller text, so a call runs in an empty folder with temp pointed there,
in its sandbox, in print mode where anything needing permission is refused, and its answer is parsed as data only. See
agy_safety_check.py and logs/agy-check for what that was tested against. Quota is read with agy's /quota command, which
starts no model turn. A limit, sign-in or service failure turns agy off for a while; a timeout or bad JSON is a passing
failure and stays on agy unless it repeats, so ordinary hiccups do not spend the codex allowance.
"""

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from uuid import uuid4

import harness
from harness import HarnessError, parse_response
import model_breaker

ROOT = Path(__file__).resolve().parent
DEFAULTS = {"agy_enabled": True, "agy_model": "gemini-3.8-flash-low", "min_remaining_percent": 15, "quota_read_interval_seconds": 300,
            "max_quota_age_seconds": 1800, "quota_read_timeout_seconds": 20, "limit_off_seconds": 3600, "service_off_seconds": 900,
            "repeat_failures_to_switch": 3, "repeat_failure_off_seconds": 900, "max_prompt_chars": 25000, "print_timeout_seconds": 70}
ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["action", "price", "reason"],
                 "properties": {"action": {"type": "string", "enum": ["ask", "accept", "reject", "offer", "walk_away"]},
                                "price": {"type": ["integer", "null"]}, "reason": {"type": "string"}}}
LIMIT_WORDS = ("resource_exhausted", "resource exhausted", "quota", "rate limit", "rate_limit", "usage limit", "limit reached", "too many requests",
               "429", "credits", "exhausted")
SERVICE_WORDS = ("unauthenticated", "unauthorized", "sign in", "log in", "login", "not logged", "credential", "403", "401", "503", "unavailable",
                 "connection", "network", "dns", "certificate")
_last_call = None


def last_call():
    """Provider, model and why, for the most recent answer in this process."""
    return dict(_last_call) if _last_call else None


def load_config(root=ROOT):
    """A value that cannot be used keeps its default. Only an explicit false turns agy off, and Claude or GPT names are refused."""
    try:
        loaded = json.loads((Path(root) / "model_router.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = {}
    settings = dict(DEFAULTS)
    if not isinstance(loaded, dict):
        return settings
    if loaded.get("agy_enabled") is False:
        settings["agy_enabled"] = False
    model = loaded.get("agy_model")
    if isinstance(model, str) and model.startswith("gemini-"):
        settings["agy_model"] = model
    for key, default in DEFAULTS.items():
        value = loaded.get(key)
        if type(default) is int and type(value) in (int, float) and math.isfinite(value) and value > 0:
            settings[key] = value
    return settings


def _state_path(root):
    return Path(root) / ".market/model-router.json"


def read_state(root=ROOT):
    try:
        state = json.loads(_state_path(root).read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(root, state):
    path = _state_path(root)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".model-router.", suffix=".tmp")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(state, stream)
        os.replace(temporary, path)
    except OSError:
        pass


def _note(root, role, state, on, reason, now):
    """Say so once per transition, in the terminal and a log."""
    if state.get("agy_on") == on and state.get("agy_reason") == reason:
        return
    state.update(agy_on=on, agy_reason=reason)
    print(f"[model] {role}: agy {'on' if on else 'off'} ({reason})", flush=True)
    try:
        path = Path(root) / "logs/model-router.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": datetime.fromtimestamp(now, timezone.utc).isoformat(), "role": role, "agy": "on" if on else "off", "reason": reason}) + "\n")
    except OSError:
        pass


def executable():
    found = shutil.which("agy") or shutil.which("agy.exe")
    if found:
        return found
    candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "agy/bin/agy.exe"
    return str(candidate) if candidate.is_file() else None


def build_agy_command(agy, prompt, model, print_timeout):
    """The whole permission surface. There is no permission-skipping flag and no settings override, by design."""
    return [agy, "--print", prompt, "--model", model, "--output-format", "json", "--sandbox", "--disable-slash-commands",
            "--print-timeout", f"{int(print_timeout)}s", "--json-schema", json.dumps(ANSWER_SCHEMA, separators=(",", ":"))]


def parse_quota(output):
    """Keep only remaining fractions and reset times of the Gemini model group."""
    groups = (((output or {}).get("command") or {}).get("data") or {}).get("groups")
    if not isinstance(groups, list):
        return None
    group = next((g for g in groups if isinstance(g, dict) and "gemini" in str(g.get("name")).lower() and "models" in str(g.get("name")).lower()), None)
    buckets = group.get("buckets") if group else None
    if not isinstance(buckets, list):
        return None
    usage = {}
    for bucket in buckets:
        window = bucket.get("window") if isinstance(bucket, dict) else None
        remaining = bucket.get("remaining_fraction") if isinstance(bucket, dict) else None
        if window in ("weekly", "5h") and type(remaining) in (int, float):
            label = "weekly" if window == "weekly" else "five_hour"
            usage[f"{label}_remaining_percent"] = round(remaining * 100, 2)
            usage[f"{label}_resets_at"] = bucket.get("reset_time")
    return usage if "weekly_remaining_percent" in usage and "five_hour_remaining_percent" in usage else None


def read_quota(timeout=DEFAULTS["quota_read_timeout_seconds"], agy=None):
    """Read agy's quota with its read-only command, which uses no tokens. Returns None when it cannot."""
    agy = agy or executable()
    if not agy:
        return None
    try:
        with tempfile.TemporaryDirectory() as work:
            done = subprocess.run([agy, "--print", "/quota", "--output-format", "json"], cwd=work, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=timeout)
        return parse_quota(json.loads(done.stdout.decode("utf-8", "replace")))
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _epoch(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def agy_status(root=ROOT, role="buyer", now=None, reader=None):
    """Return (usable, reason). Never raises; any problem here means agy is skipped for this call."""
    now = time.time() if now is None else now
    try:
        settings = load_config(root)
        state = read_state(root)
        if not settings["agy_enabled"]:
            usable, reason = False, "agy_disabled_in_config"
        elif type(state.get("agy_off_until")) in (int, float) and state["agy_off_until"] > now:
            usable, reason = False, state.get("agy_off_reason", "agy_off")
        else:
            state.pop("agy_off_until", None)
            quota = state.get("quota") if isinstance(state.get("quota"), dict) else None
            read_at = quota.get("read_at") if quota else None
            tried_at = state.get("quota_tried_at")
            due = type(read_at) not in (int, float) or now - read_at >= settings["quota_read_interval_seconds"]
            retry_due = type(tried_at) not in (int, float) or now - tried_at >= settings["quota_read_interval_seconds"]
            if due and retry_due:
                state["quota_tried_at"] = now
                fresh = (reader or read_quota)(settings["quota_read_timeout_seconds"])
                if fresh:
                    quota = dict(fresh, read_at=now)
                    state["quota"] = quota
            read_at = quota.get("read_at") if quota else None
            floor = settings["min_remaining_percent"]
            if type(read_at) not in (int, float):
                usable, reason = False, "agy_quota_unreadable"
            elif now - read_at > settings["max_quota_age_seconds"]:
                usable, reason = False, "agy_quota_stale"
            elif quota.get("five_hour_remaining_percent", 0) < floor:
                usable, reason = False, "agy_five_hour_low"
            elif quota.get("weekly_remaining_percent", 0) < floor:
                usable, reason = False, "agy_weekly_low"
            else:
                usable, reason = True, "ok"
        _note(root, role, state, usable, reason, now)
        _write_state(root, state)
        return usable, reason
    except Exception as error:
        print(f"[model] {role}: agy skipped (router_error: {type(error).__name__})", flush=True)
        return False, "router_error"


def available(root=ROOT, role="buyer", now=None, reader=None):
    """Whether any model can answer. A caller with no model uses its coded rules."""
    usable, reason = agy_status(root, role, now, reader)
    if usable:
        return True, "agy"
    allowed, codex_reason = model_breaker.check(root, role, now)
    return (True, "codex") if allowed else (False, f"{reason}+{codex_reason}")


class AgyTransient(RuntimeError):
    """A timeout or a malformed answer: retry rules apply."""


class AgyUnavailable(RuntimeError):
    def __init__(self, message, kind):
        super().__init__(message)
        self.kind = kind


def classify_failure(text):
    lowered = (text or "").lower()
    if any(word in lowered for word in LIMIT_WORDS):
        return "limit"
    if any(word in lowered for word in SERVICE_WORDS):
        return "service"
    return "unknown"


def extract_answer(output):
    """Prefer the schema-checked object, then the text. Tool requests and prose never count as an answer."""
    structured = output.get("structured_output")
    if isinstance(structured, dict):
        return parse_response(json.dumps(structured))
    text = str(output.get("response") or "").strip()
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()
    return parse_response(text)


def run_agy(prompt, timeout, settings, root=ROOT, runner=None):
    """One agy call with every output kept. Returns (answer, model). Raises AgyTransient or AgyUnavailable."""
    agy = executable() if runner is None else "agy"
    if not agy:
        raise AgyUnavailable("agy executable not found", "service")
    model = settings["agy_model"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    log_dir = Path(root) / "logs/harness" / (run_id + "-agy")
    log_dir.mkdir(parents=True)
    work_dir = Path(root) / ".harness-work" / (run_id + "-agy")
    work_dir.mkdir(parents=True)
    (log_dir / "request.txt").write_text(prompt, encoding="utf-8")
    print_timeout = max(5, min(settings["print_timeout_seconds"], timeout - 5))
    command = build_agy_command(agy, prompt, model, print_timeout)
    logged = [part if part != prompt else "<prompt in request.txt>" for part in command]
    metadata = {"provider": "agy", "model": model, "cwd": str(work_dir), "timeout_seconds": timeout, "status": "running", "command": logged}
    started = time.monotonic()
    env = dict(os.environ, TEMP=str(work_dir), TMP=str(work_dir))
    try:
        try:
            done = (runner or subprocess.run)(command, cwd=work_dir, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, env=env)
        except subprocess.TimeoutExpired as error:
            (log_dir / "stdout.txt").write_bytes(error.stdout or b"")
            (log_dir / "stderr.txt").write_bytes(error.stderr or b"")
            raise AgyTransient(f"agy exceeded {timeout} seconds") from error
        except OSError as error:
            raise AgyUnavailable(f"agy could not start: {error}", "service") from error
        stdout, stderr = done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")
        (log_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
        (log_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
        metadata["returncode"] = done.returncode
        try:
            output = json.loads(stdout)
        except ValueError:
            output = None
        if isinstance(output, dict):
            metadata.update(tokens=(output.get("usage") or {}).get("total_tokens"), agy_status=output.get("status"),
                            finished_reason=output.get("finished_reason"), denied_actions=output.get("denied_actions"))
        finished = (output or {}).get("finished_reason") if isinstance(output, dict) else None
        if finished == "print_timeout":
            raise AgyTransient("agy print timeout")
        if done.returncode != 0 or not isinstance(output, dict) or output.get("status") != "SUCCESS" or finished == "error":
            detail = (stderr + "\n" + stdout)[-2000:]
            raise AgyUnavailable(f"agy failed with code {done.returncode}", classify_failure(detail))
        try:
            answer = extract_answer(output)
        except ValueError as error:
            raise AgyTransient(f"agy answer was not valid: {error}") from error
        metadata["status"] = "ok"
        (log_dir / "result.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
        return answer, model
    except (AgyTransient, AgyUnavailable) as error:
        metadata["status"] = "failed"
        metadata["error"] = str(error)
        metadata["failure_kind"] = getattr(error, "kind", "transient")
        raise
    finally:
        metadata["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (log_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def _turn_agy_off(root, role, settings, state, kind, now, reader=None):
    seconds = {"limit": settings["limit_off_seconds"], "repeat": settings["repeat_failure_off_seconds"]}.get(kind, settings["service_off_seconds"])
    until = now + seconds
    reason = f"agy_{kind}_failure"
    if kind == "limit":
        fresh = (reader or read_quota)(settings["quota_read_timeout_seconds"])
        if fresh:
            state["quota"] = dict(fresh, read_at=now)
            spent = [_epoch(fresh.get(f"{label}_resets_at")) for label in ("five_hour", "weekly") if fresh.get(f"{label}_remaining_percent", 100) < 1]
            spent = [moment for moment in spent if moment and moment > now]
            if spent:
                until = max(spent)
    state.update(agy_off_until=until, agy_off_reason=reason, agy_transient_failures=0)
    _note(root, role, state, False, reason, now)
    _write_state(root, state)


def run_model(provider, prompt, timeout=90, role="buyer", root=ROOT, runner=None, reader=None):
    """agy, then codex. Raises HarnessError when neither answers, so the caller falls back to its coded rules."""
    global _last_call
    _last_call = None
    if provider != "codex":
        return harness.run_harness(provider, prompt, timeout=timeout)
    now = time.time()
    settings = load_config(root)
    switch_reason = None
    usable, why = agy_status(root, role, now, reader)
    if usable and len(prompt) > settings["max_prompt_chars"]:
        usable, why = False, "prompt_too_long"
    if usable:
        state = read_state(root)
        try:
            answer, model = run_agy(prompt, timeout, settings, root, runner)
            state["agy_transient_failures"] = 0
            _write_state(root, state)
            _last_call = {"provider": "agy", "model": model, "route_reason": "agy_first"}
            return answer
        except AgyTransient as error:
            failures = int(state.get("agy_transient_failures") or 0) + 1
            state["agy_transient_failures"] = failures
            if failures >= settings["repeat_failures_to_switch"]:
                _turn_agy_off(root, role, settings, state, "repeat", now, reader)
                switch_reason = "agy_repeat_failure"
            else:
                _write_state(root, state)
                failure = HarnessError(str(error))
                failure.__cause__ = error.__cause__ if isinstance(error.__cause__, ValueError) else None
                raise failure from failure.__cause__
        except AgyUnavailable as error:
            _turn_agy_off(root, role, settings, state, error.kind, now, reader)
            switch_reason = f"agy_{error.kind}_failure"
    allowed, codex_reason = model_breaker.check(root, role, now)
    if not allowed:
        raise HarnessError(f"No model available: {switch_reason or why}; codex {codex_reason}")
    answer = harness.run_harness("codex", prompt, timeout=timeout)
    _last_call = {"provider": "codex", "model": harness.MODELS["codex"], "route_reason": switch_reason or why}
    return answer
