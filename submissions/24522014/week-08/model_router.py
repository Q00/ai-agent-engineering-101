"""Model order: agy, then codex, then the caller's coded rules. Agy calls run with no way to act on the machine.

agy is a tool-using agent and these prompts carry seller text, so a call runs in an empty folder with temp pointed there,
in its sandbox, with file, command, URL and MCP rules denied, and its answer is parsed as data only. agy has no way to turn
off search_web and seven other tools, which the owner accepted knowingly, so tool use is read from the event stream: any
tool use discards the answer, and anything other than search_web or a refused file call turns agy off. Quota is read with
agy's /quota command, which starts no model turn. A limit, sign-in or service failure turns agy off for a while; a timeout
or bad JSON is a passing failure and stays on agy unless it repeats, so ordinary hiccups do not spend the codex allowance.
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

SECRET_ENV_WORDS = ("KEY", "TOKEN", "SECRET", "PASSW", "CREDENTIAL", "CLAIM", "COOKIE", "OWNER", "AUTH")
AGY_OWN_ENV = ("AGY_", "ANTIGRAVITY_", "GEMINI_", "GOOGLE_")

ROOT = Path(__file__).resolve().parent
DEFAULTS = {"agy_enabled": True, "agy_model": "gemini-4-argon-low", "min_remaining_percent": 15, "quota_read_interval_seconds": 300,
            "max_quota_age_seconds": 1800, "quota_read_timeout_seconds": 20, "limit_off_seconds": 3600, "service_off_seconds": 900,
            "repeat_failures_to_switch": 3, "repeat_failure_off_seconds": 900, "max_prompt_chars": 25000, "print_timeout_seconds": 70}
ANSWER_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["action", "price", "reason"],
                 "properties": {"action": {"type": "string", "enum": ["ask", "accept", "reject", "offer", "walk_away"]},
                                "price": {"type": ["integer", "null"]}, "reason": {"type": "string"}}}
DENY_SETTINGS = {"permissions": {"deny": ["read_file(*)", "write_file(*)", "command(*)", "read_url(*)", "execute_url(*)", "mcp(*)"]}}
NO_TOOLS_NOTE = "\n\nDo not call any tools. Answer only from the input data above, as one JSON object."
FILE_TOOLS = {"read_file", "write_file", "edit_file", "run_command"}
ACCEPTED_TOOLS = {"search_web"}
REFUSED_STATES = {"ERROR", "ACTIVE", "PENDING"}
STOP_SECONDS = 3650 * 86400
DISCARDED = {"action": "walk_away", "price": None, "reason": "Model used a tool; its answer was discarded."}
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
    return [agy, "--print", prompt, "--model", model, "--output-format", "stream-json", "--sandbox", "--disable-slash-commands",
            "--print-timeout", f"{int(print_timeout)}s", "--json-schema", json.dumps(ANSWER_SCHEMA, separators=(",", ":")),
            "--settings", json.dumps(DENY_SETTINGS, separators=(",", ":"))]


def parse_quota(output, model=None):
    """Keep only remaining fractions and reset times. Argon has its own weekly group and no five-hour one of its own,
    so its five-hour figure comes from the Gemini group, which also fell during Argon calls."""
    groups = (((output or {}).get("command") or {}).get("data") or {}).get("groups")
    if not isinstance(groups, list):
        return None
    wanted = [lambda name: "gemini" in name and "models" in name]
    if "argon" in str(model):
        wanted.insert(0, lambda name: "argon" in name)
    usage = {}
    for match in wanted:
        group = next((g for g in groups if isinstance(g, dict) and match(str(g.get("name")).lower())), None)
        buckets = group.get("buckets") if group else None
        if not isinstance(buckets, list):
            return None
        for bucket in buckets:
            window = bucket.get("window") if isinstance(bucket, dict) else None
            remaining = bucket.get("remaining_fraction") if isinstance(bucket, dict) else None
            label = {"weekly": "weekly", "5h": "five_hour"}.get(window)
            if label and type(remaining) in (int, float) and f"{label}_remaining_percent" not in usage:
                usage[f"{label}_remaining_percent"] = round(remaining * 100, 2)
                usage[f"{label}_resets_at"] = bucket.get("reset_time")
    return usage if "weekly_remaining_percent" in usage and "five_hour_remaining_percent" in usage else None


def agy_env(base=None, **extra):
    """The environment for an agy process. Its prompts carry seller text and it holds a web tool, so the kit's claim code and every other secret stay out. Everything else stays, and so do agy's own sign-in variables."""
    base = os.environ if base is None else base
    def hidden(name):
        upper = name.upper()
        if upper.startswith(AGY_OWN_ENV):
            return False
        return upper.startswith("MARKET_") or any(word in upper for word in SECRET_ENV_WORDS)
    return {**{name: value for name, value in base.items() if not hidden(name)}, **extra}


def read_quota(timeout=DEFAULTS["quota_read_timeout_seconds"], agy=None, model=None):
    """Read agy's quota with its read-only command, which uses no tokens. Returns None when it cannot."""
    agy = agy or executable()
    if not agy:
        return None
    try:
        with tempfile.TemporaryDirectory() as work:
            done = subprocess.run([agy, "--print", "/quota", "--output-format", "json"], cwd=work, stdin=subprocess.DEVNULL,
                                  capture_output=True, timeout=timeout, env=agy_env())
        return parse_quota(json.loads(done.stdout.decode("utf-8", "replace")), model)
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
        lift_finish_stop(root, role, state, now)
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
                fresh = reader(settings["quota_read_timeout_seconds"]) if reader else read_quota(settings["quota_read_timeout_seconds"], model=settings["agy_model"])
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


def read_stream(stdout):
    """The result object and the tools the run tried, by name and state. Tool inputs are not kept."""
    result, tools, steps = None, {}, []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("event") == "result" and isinstance(event.get("result"), dict):
            result = event["result"]
        step = event.get("step_update")
        if isinstance(step, dict):
            steps.append(step)
    for step in steps:
        if step.get("step_type") == "tool" and step.get("tool_name") and not is_closing_finish(step, steps, result):
            tools.setdefault(str(step["tool_name"]), set()).add(str(step.get("state")))
    return result, tools


def is_closing_finish(step, steps, result):
    """agy ends every run with a finish step, and now and then also reports it as a tool. That is the answer being handed in:
    the last step of a successful run, carrying exactly the final answer. A finish looking any other way stays an unknown tool."""
    index = step.get("step_index")
    return (step.get("tool_name") == "finish" and type(index) is int and step.get("state") in ("ACTIVE", "DONE")
            and index == max((s["step_index"] for s in steps if type(s.get("step_index")) is int))
            and any(s.get("step_type") == "finish" and s.get("step_index") == index and s.get("state") == "DONE" for s in steps)
            and isinstance(result, dict) and result.get("status") == "SUCCESS" and isinstance(result.get("structured_output"), dict)
            and (step.get("tool_info") or {}).get("parameters") == result["structured_output"])


def stopped_by_finish_alone(root, state):
    """Whether the stored stop came from the alert that named finish and nothing else. Any other or missing evidence keeps it."""
    until = state.get("agy_off_until")
    if state.get("agy_off_reason") != "agy_unexpected_tool" or type(until) not in (int, float):
        return False
    try:
        lines = (Path(root) / "logs/model-router-alerts.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            alert = json.loads(line)
        except ValueError:
            continue
        moment = _epoch(alert.get("at")) if isinstance(alert, dict) else None
        if moment is not None and abs(moment + STOP_SECONDS - until) < 1:
            return alert.get("tools") == ["finish"]
    return False


def lift_finish_stop(root, role, state, now):
    """Undo only the stored stop; agy_enabled in model_router.json stays the owner's switch."""
    if not stopped_by_finish_alone(root, state):
        return
    state.pop("agy_off_until", None)
    state.pop("agy_off_reason", None)
    print(f"[model] {role}: stored agy stop lifted (finish alone caused it); agy_enabled unchanged", flush=True)
    try:
        path = Path(root) / "logs/model-router.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": datetime.fromtimestamp(now, timezone.utc).isoformat(), "role": role, "agy": "stop_lifted", "reason": "finish_alone"}) + "\n")
    except OSError:
        pass


def serious_tools(tools):
    """A tool beyond what the owner accepted, unknown, or a file or command call that was not refused."""
    return sorted(name for name, states in tools.items()
                  if not (name in ACCEPTED_TOOLS or (name in FILE_TOOLS and states <= REFUSED_STATES)))


def stop_agy(root, role, serious, now):
    """Keep agy off until someone turns it back on, and leave a notice. Nothing the tool made is touched."""
    try:
        path = Path(root) / "model_router.json"
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
            settings = settings if isinstance(settings, dict) else {}
        except (OSError, ValueError):
            settings = {}
        settings["agy_enabled"] = False
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".model_router.", suffix=".tmp")
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(settings, stream, indent=2)
        os.replace(temporary, path)
    except OSError as error:
        print(f"[model] ALERT {role}: could not write agy_enabled=false ({type(error).__name__})", flush=True)
    state = read_state(root)
    state.update(agy_off_until=now + STOP_SECONDS, agy_off_reason="agy_unexpected_tool")
    _note(root, role, state, False, "agy_unexpected_tool", now)
    _write_state(root, state)
    print(f"[model] ALERT {role}: agy used {serious}; agy turned off, answer discarded", flush=True)
    try:
        alerts = Path(root) / "logs/model-router-alerts.log"
        alerts.parent.mkdir(parents=True, exist_ok=True)
        with alerts.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": datetime.fromtimestamp(now, timezone.utc).isoformat(), "role": role, "tools": serious}) + "\n")
    except OSError:
        pass


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
    """One agy call with every output kept. Returns (answer, model, tools tried). Raises AgyTransient or AgyUnavailable."""
    agy = executable() if runner is None else "agy"
    if not agy:
        raise AgyUnavailable("agy executable not found", "service")
    model = settings["agy_model"]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid4().hex[:8]
    log_dir = Path(root) / "logs/harness" / (run_id + "-agy")
    log_dir.mkdir(parents=True)
    work_dir = Path(root) / ".harness-work" / (run_id + "-agy")
    work_dir.mkdir(parents=True)
    prompt += NO_TOOLS_NOTE
    (log_dir / "request.txt").write_text(prompt, encoding="utf-8")
    print_timeout = max(5, min(settings["print_timeout_seconds"], timeout - 5))
    command = build_agy_command(agy, prompt, model, print_timeout)
    logged = [part if part != prompt else "<prompt in request.txt>" for part in command]
    metadata = {"provider": "agy", "model": model, "cwd": str(work_dir), "timeout_seconds": timeout, "status": "running", "command": logged}
    started = time.monotonic()
    env = agy_env(TEMP=str(work_dir), TMP=str(work_dir))
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
        output, tools = read_stream(stdout)
        metadata["tools_used"] = sorted(tools)
        if isinstance(output, dict):
            metadata.update(tokens=(output.get("usage") or {}).get("total_tokens"), agy_status=output.get("status"),
                            finished_reason=output.get("finished_reason"), denied_actions=output.get("denied_actions"))
        finished = (output or {}).get("finished_reason") if isinstance(output, dict) else None
        if finished == "print_timeout":
            raise AgyTransient("agy print timeout")
        if tools:
            # Never use an answer from a run that touched a tool, even when the tool was refused.
            metadata.update(status="ok", answer_discarded=True, tool_states={name: sorted(states) for name, states in tools.items()})
            return None, model, tools
        if done.returncode != 0 or not isinstance(output, dict) or output.get("status") != "SUCCESS" or finished == "error":
            detail = (stderr + "\n" + stdout)[-2000:]
            raise AgyUnavailable(f"agy failed with code {done.returncode}", classify_failure(detail))
        try:
            answer = extract_answer(output)
        except ValueError as error:
            raise AgyTransient(f"agy answer was not valid: {error}") from error
        metadata["status"] = "ok"
        (log_dir / "result.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2), encoding="utf-8")
        return answer, model, {}
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
        fresh = reader(settings["quota_read_timeout_seconds"]) if reader else read_quota(settings["quota_read_timeout_seconds"], model=settings["agy_model"])
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
            answer, model, tools = run_agy(prompt, timeout, settings, root, runner)
            state["agy_transient_failures"] = 0
            _write_state(root, state)
            if tools:
                serious = serious_tools(tools)
                if serious:
                    stop_agy(root, role, serious, now)
                _last_call = {"provider": "agy", "model": model, "route_reason": "agy_unexpected_tool" if serious else "agy_tool_use_discarded",
                              "tools_used": sorted(tools)}
                return dict(DISCARDED)
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
