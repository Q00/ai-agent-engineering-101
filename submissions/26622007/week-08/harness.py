"""Asks the harness for one decision and returns a JSON answer that matches a schema.

The seller and the buyer both decide through ask(). Three backends:

- HARNESS=codex (the market account on the Mac mini until 11/06): gpt-6-luna through
  `codex exec` on the ChatGPT subscription login, reasoning effort low, the schema
  passed as --output-schema. Each call runs in an empty directory outside any repository,
  one fixed per tag (codex_workdir) so the prompt codex builds keeps one prefix, with a
  read-only sandbox, approvals never, the user config and rules ignored, the shell
  and every other built-in tool turned off, and an environment holding no key (only
  PATH, HOME, locale). At most CODEX_CONCURRENCY calls run at once over all processes
  of a market dir (slot lock files); a call waits up to CODEX_QUEUE_WAIT s for a slot.
  There is no call cap. A failure raises HarnessError, so each caller falls back to
  its rules; it never falls over to OpenRouter.
- HARNESS=claude (default, used by the trade test): Claude Code, `claude -p --model haiku`.
  Every call runs in a fresh empty directory so Claude Code does not pick up this
  repository's CLAUDE.md, and passes --json-schema so the answer comes back as
  structured output. Needs a `claude` login on this machine.
- HARNESS=openrouter (agent.json since 11/06: codex got 429 and 35 s retries there):
  openai/gpt-6-luna on OpenRouter with OPENROUTER_API_KEY from the environment or
  the submission's .env, within OR_TIMEOUT s per decision (or the caller's timeout),
  pinned to the OpenAI endpoint without fallbacks, require_parameters so an
  unsupported parameter fails instead of being dropped, reasoning effort low (none for
  the buyer's offer turns, TAG_REASONING), no
  temperature, and response_format = strict json_schema on every request. The body
  is read back as sent and its relevant fields are logged; the key never is.

Either way the answer is validated again here (fields, types, enums) before use.
"""
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import httpx

BACKEND = os.environ.get("HARNESS", "claude")
MODEL = "haiku"
BASE_CMD = ["claude", "-p", "--model", MODEL, "--tools", "", "--setting-sources", "",
            "--no-session-persistence", "--output-format", "json"]
OR_URL = "https://openrouter.ai/api/v1/chat/completions"
OR_MODEL = os.environ.get("OR_MODEL", "openai/gpt-6-luna")
OR_PROVIDER = os.environ.get("OR_PROVIDER", "openai")
OR_REASONING = os.environ.get("OR_REASONING", "low")
# The buyer's offer turns at effort none (luna takes none..max on OpenRouter, no minimal): 80 logged turns replayed 11/07
# 03:40 took a median 1.22 s (p90 1.58) at none against 4.19 s (p90 4.99) at low, the same action in 80 of 80, the same
# shop price read in 80 of 80, offers 0.873 against 0.872 of the limit. OR_REASONING_BUYER=low brings low back.
TAG_REASONING = {"buyer-harness": os.environ.get("OR_REASONING_BUYER", "none")}
# Off by default (a person's call, 11/05): at 300 the long-lived seller gave up thousands of negotiations a day. Luna is
# cheap; calls and cost stay measured (CALLS, llm_calls.jsonl). OR_MAX_CALLS_PER_HOUR=300 in the environment brings it back.
OR_MAX_CALLS_PER_HOUR = int(os.environ.get("OR_MAX_CALLS_PER_HOUR", "0"))   # 0 or less: no hourly cap
TIMEOUT = 90
# One OpenRouter decision's whole budget (both tries and the back-off), like CODEX_TIMEOUT; a caller's timeout wins.
OR_TIMEOUT = int(os.environ.get("OR_TIMEOUT", "60"))
RETRIES = 2
# A tag here gets this many seconds per OpenRouter try and TRY_RETRIES tries in its OR_TIMEOUT, instead of one try that
# may use it all. The buyer's offers took a median 4.2 s, but 30 of 258 took 30-60 s (11/06 17:50-19:50), and those
# held a whole order for the one slow answer (talk p90 190 s); a fresh try answers in about 4 s.
TRY_SECONDS = {"buyer-harness": 12}
TRY_RETRIES = 3
_or_calls = []   # start times of OpenRouter calls in the last hour: per process, so each market_buyer run starts at 0
CALLS = {"attempts": 0, "ok": 0, "cap": 0}   # this process's requests (measure.py orders.jsonl)

CODEX_MODEL = os.environ.get("CODEX_MODEL", "gpt-6-luna")
CODEX_REASONING = os.environ.get("CODEX_REASONING", "low")
CODEX_TIMEOUT = int(os.environ.get("CODEX_TIMEOUT", "60"))
CODEX_CONCURRENCY = int(os.environ.get("CODEX_CONCURRENCY", "6"))
CODEX_QUEUE_WAIT = float(os.environ.get("CODEX_QUEUE_WAIT", "30"))
CHATGPT_CODEX = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
# Built-in tools a decision never needs; off so that text from other agents cannot make Luna run anything.
CODEX_OFF = ("shell_tool", "unified_exec", "apps", "plugins", "multi_agent", "image_generation", "browser_use",
             "computer_use", "goals", "view_image", "skill_search", "tool_suggest", "hooks", "in_app_browser")
# Some Codex tools stay on whatever is disabled (exec, wait, collaboration, mcp resources: 5 of 174 live calls 11/05
# used one, in the agent loop whose message lists market tools). Said plainly, and any run that used one is not believed.
CODEX_OFF += ("code_mode_host", "sleep_tool")
CODEX_NO_TOOLS = ("\n\nAnswer with the JSON object only, from what the message says. Call no function or tool of this "
                  "session: tools the message lists are used only through the fields of your JSON answer.")
CODEX_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "CODEX_HOME")
# The subscription saying no (usage limit, login gone, model refused): another try within seconds says it again.
CODEX_FINAL = re.compile(r"usage[ _]limit|rate[ _-]?limit|quota|\b429\b|not supported|log ?in|unauthori[sz]ed|\b40[13]\b",
                         re.I)
_codex_path = []
# Fixed empty cwd per tag (codex_workdir): the user's temp folder, which no repository sits above.
CODEX_CWD_ROOT = os.environ.get("CODEX_CWD_ROOT") or os.path.join(os.path.realpath(tempfile.gettempdir()),
                                                                  f"harness-codex-cwd-{os.getuid()}")


class HarnessError(RuntimeError):
    pass


def log(tag, msg):
    print(f"[{time.strftime('%H:%M:%S')}] {tag} {msg}", file=sys.stderr, flush=True)


def validate(answer, schema):
    """Local check of the structured output: required fields, types, enums."""
    if not isinstance(answer, dict):
        raise HarnessError(f"answer is not an object: {answer!r}")
    props = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in answer:
            raise HarnessError(f"missing field {key!r} in {answer!r}")
    for key, value in answer.items():
        spec = props.get(key)
        if spec is None:
            raise HarnessError(f"unexpected field {key!r}")
        kind = spec.get("type")
        if kind == "integer" and not (isinstance(value, int) and not isinstance(value, bool)):
            raise HarnessError(f"{key}={value!r} is not an integer")
        if kind == "string" and not isinstance(value, str):
            raise HarnessError(f"{key}={value!r} is not a string")
        if "enum" in spec and value not in spec["enum"]:
            raise HarnessError(f"{key}={value!r} not in {spec['enum']}")
    return answer


def ask(prompt, schema, system, tag="harness", timeout=None):
    """One decision. Returns the validated structured output as a dict. timeout (codex, openrouter): this call's limit in
    seconds instead of CODEX_TIMEOUT / OR_TIMEOUT, for a caller with a deadline of its own (the buyer's shop choice)."""
    if BACKEND == "codex":
        return _ask_codex(prompt, schema, system, tag, timeout)
    if BACKEND == "openrouter":
        return _ask_openrouter(prompt, schema, system, tag, timeout)
    if BACKEND != "claude":
        raise HarnessError(f"unknown HARNESS={BACKEND!r}")
    return _ask_claude(prompt, schema, system, tag)


def record_call(tag, prompt, system, seconds, ok, usage=None):
    """One line per request in .market/llm_calls.jsonl, so what each kind of call costs can be measured."""
    CALLS["attempts"] += 1
    CALLS["ok"] += bool(ok)
    md = os.environ.get("MARKET_DIR")
    if not md:
        return
    usage = usage or {}
    # codex reports cached_input_tokens (kept as cached_tokens); OpenRouter puts it in prompt_tokens_details
    cached = usage.get("cached_tokens")
    if cached is None:
        cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
    try:
        with open(os.path.join(md, "llm_calls.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "tag": tag, "backend": BACKEND, "chars": len(prompt) + len(system),
                                "seconds": round(seconds, 2), "ok": ok,
                                "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"),
                                "cached_tokens": cached, "cost": usage.get("cost")}) + "\n")
    except OSError:
        pass


def _log_wire(tag):
    def hook(request):
        """The body as it leaves the client. The Authorization header is never read."""
        try:
            body = json.loads(request.content or b"{}")
        except ValueError:
            body = {}
        rf = body.get("response_format") or {}
        log(tag, f"wire model={body.get('model')} response_format={rf.get('type')}"
                 f"/strict={rf.get('json_schema', {}).get('strict')} provider={body.get('provider')} "
                 f"reasoning={body.get('reasoning')} temperature_sent={'temperature' in body}")
    return hook


def _openrouter_key():
    """OPENROUTER_API_KEY from the environment, else from the submission's .env (OPENROUTER_ENV_FILE, default ../.env
    next to this file), so agent.json can switch HARNESS without putting the key on a command line."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    path = os.environ.get("OPENROUTER_ENV_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                name, sep, value = line.strip().partition("=")
                if sep and name.strip().removeprefix("export ").strip() == "OPENROUTER_API_KEY":
                    return value.strip().strip("'\"") or None
    except OSError:
        return None
    return None


# Top-level OpenRouter body keys the outer_json diagnostic may name; any other key is only counted.
BODY_KEYS = ("choices", "created", "error", "id", "model", "object", "provider", "system_fingerprint", "usage")
CHOICE_KEYS = ("delta", "error", "finish_reason", "index", "logprobs", "message", "native_finish_reason")
MESSAGE_KEYS = ("content", "reasoning", "refusal", "role", "tool_calls")
FINISH_REASONS = ("content_filter", "error", "length", "stop", "tool_calls")


def _body_shape(body):
    """What a 200 body without choices[0].message.content looked like: its known keys, how many others, an integer
    error code (OpenRouter sends upstream failures as {"error": {"code": 502, ...}} with HTTP 200) and the number of
    choices. No value text: 45 such answers on 11/10 07:30-08:28 left only "shape" in the log."""
    if not isinstance(body, dict):
        return {}
    shape = {"body_keys": sorted(k for k in body if k in BODY_KEYS),
             "other_keys": sum(1 for k in body if k not in BODY_KEYS)}
    code = body["error"].get("code") if isinstance(body.get("error"), dict) else None
    if type(code) is int and 100 <= code <= 999:
        shape["error_code"] = code
    if isinstance(body.get("choices"), list):
        shape["choices"] = len(body["choices"])
        first = body["choices"][0] if body["choices"] else None
        if isinstance(first, dict):   # an upstream failure mid-answer comes as choices[0].error / finish_reason "error"
            shape["choice_keys"] = sorted(k for k in first if k in CHOICE_KEYS)
            shape["choice_other_keys"] = sum(1 for k in first if k not in CHOICE_KEYS)
            code = first["error"].get("code") if isinstance(first.get("error"), dict) else None
            if type(code) is int and 100 <= code <= 999:
                shape["choice_error_code"] = code
            if "finish_reason" in first:
                reason = first["finish_reason"]
                shape["finish_reason"] = "null" if reason is None else reason if reason in FINISH_REASONS else "other"
            if isinstance(first.get("message"), dict):
                shape["message_keys"] = sorted(k for k in first["message"] if k in MESSAGE_KEYS)
    return shape


def _parse_diagnostic(stage, error, tag, response=None):
    """Best-effort allowlisted metadata; never expose the answer or exception text."""
    try:
        if stage not in ("outer_json", "inner_json", "schema_validation"):
            return
        roles = ("restock-agent", "forum-agent", "seller-harness", "buyer-harness", "buyer-agent")
        meta = {"stage": stage, "role": tag if tag in roles else "other",
                "kind": "schema" if stage == "schema_validation" else "shape"}
        if isinstance(error, json.JSONDecodeError):
            doc, pos = error.doc, error.pos
            end = pos
            while end < len(doc) and doc[end] in " \t\r\n":
                end += 1
            char = doc[end:end + 1]
            tail = ("none" if not char else "object" if char == "{" else
                    "array" if char == "[" else "string" if char == '"' else
                    "number" if char in "-0123456789" else
                    "literal" if char in "tfn" else "other")
            meta.update(kind="json_decode", pos=pos, lineno=error.lineno, colno=error.colno,
                        chars=len(doc), sha256=hashlib.sha256(doc.encode("utf-8", "surrogatepass")).hexdigest(),
                        tail_kind=tail)
        elif stage == "outer_json":
            meta["exc"] = type(error).__name__ if type(error) in (KeyError, IndexError, TypeError) else "other"
            try:
                meta.update(_body_shape(response.json()) if response is not None else {})
            except Exception:   # noqa: BLE001 - the stage and kind are still worth a line
                pass
        log("harness", "parse_diag " + json.dumps(meta, sort_keys=True))
    except Exception:
        # Observability must not replace the existing reject/retry outcome.
        pass


def _ask_openrouter(prompt, schema, system, tag, timeout=None):
    key = _openrouter_key()
    if not key:
        raise HarnessError("HARNESS=openrouter needs OPENROUTER_API_KEY in the environment or OPENROUTER_ENV_FILE")
    if OR_MAX_CALLS_PER_HOUR > 0:
        now = time.time()
        _or_calls[:] = [t for t in _or_calls if now - t < 3600]
        if len(_or_calls) >= OR_MAX_CALLS_PER_HOUR:
            CALLS["cap"] += 1
            try:
                import measure
                if os.environ.get("MARKET_DIR"):
                    measure.write(os.environ["MARKET_DIR"], "llm_cap", {"tag": tag, "pid": os.getpid(), "cap": OR_MAX_CALLS_PER_HOUR})
            except Exception:   # noqa: BLE001 - the caller expects the HarnessError below, not a measurement error
                pass
            raise HarnessError(f"call cap reached: {OR_MAX_CALLS_PER_HOUR} OpenRouter calls in the last hour")
        _or_calls.append(now)
    body = {
        "model": OR_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "decision", "strict": True, "schema": schema}},
        "reasoning": {"effort": TAG_REASONING.get(tag, OR_REASONING)},
        "provider": {"only": [OR_PROVIDER], "allow_fallbacks": False, "require_parameters": True},
    }
    last = None
    limit = timeout or OR_TIMEOUT
    per_try = TRY_SECONDS.get(tag)
    tries = TRY_RETRIES if per_try else RETRIES
    first = time.time()
    with httpx.Client(timeout=limit, event_hooks={"request": [_log_wire(tag)]}) as http:
        for attempt in range(1, tries + 1):
            left = limit - (time.time() - first)
            if attempt > 1 and left < (per_try / 2 if per_try else limit / 2):
                break          # a retry would not fit in what is left of the caller's deadline
            started = time.time()
            try:
                r = http.post(OR_URL, json=body, headers={"Authorization": f"Bearer {key}"},
                              timeout=min(left, per_try) if per_try else left)
            except httpx.HTTPError as e:
                last = f"{type(e).__name__}: {e}"
                log(tag, f"attempt {attempt}: {last}")
                record_call(tag, prompt, system, time.time() - started, False)
                continue
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:300]}"
                log(tag, f"attempt {attempt}: {last}")
                record_call(tag, prompt, system, time.time() - started, False)
                if r.status_code < 500 and r.status_code != 429:
                    break          # a 4xx (unsupported parameter, bad key) will not fix itself
                if attempt < tries:
                    time.sleep(min(3 * attempt, max(0, limit - (time.time() - first)) / 4))
                continue
            stage = "outer_json"
            try:
                content = r.json()["choices"][0]["message"]["content"]
                stage = "inner_json"
                decoded = json.loads(content)
                stage = "schema_validation"
                answer = validate(decoded, schema)
            except (KeyError, IndexError, TypeError, ValueError, HarnessError) as e:
                last = f"invalid answer: {stage}"
                elapsed = time.time() - started
                _parse_diagnostic(stage, e, tag, r if stage == "outer_json" else None)
                record_call(tag, prompt, system, elapsed, False)
                continue
            record_call(tag, prompt, system, time.time() - started, True, r.json().get("usage"))
            log(tag, f"openrouter {OR_MODEL} response_format=json_schema(strict) {time.time() - started:.1f}s "
                     f"-> {json.dumps(answer, ensure_ascii=False)}")
            return answer
    raise HarnessError(f"no valid answer after {tries} attempts: {last}")


def _version(path):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", out)
    return tuple(map(int, m.groups())) if m else None


def find_codex():
    """HARNESS_CODEX, else the newest of the codex on PATH and the ChatGPT.app one (as meta_harness.find_codex);
    found once per process, and with a single candidate no version is asked."""
    if not _codex_path:
        found = [p for p in (shutil.which("codex"), CHATGPT_CODEX) if p and os.path.exists(p)]
        if os.environ.get("HARNESS_CODEX"):
            found = [os.environ["HARNESS_CODEX"]]
        elif len(found) > 1:
            found = [p for _, p in sorted(((_version(p), p) for p in found), key=lambda vp: vp[0] or ())[::-1]]
        if not found:
            raise HarnessError("HARNESS=codex: no codex on PATH or in ChatGPT.app")
        _codex_path.append(found[0])
    return _codex_path[0]


def codex_cmd(codex, schema_file, out_file, workdir, system):
    cmd = [codex, "exec", "-m", CODEX_MODEL, "-c", f'model_reasoning_effort="{CODEX_REASONING}"',
           "-c", 'approval_policy="never"', "-c", 'web_search="disabled"',
           # a JSON string is a TOML basic string, so the system prompt arrives as written
           "-c", "developer_instructions=" + json.dumps(system + CODEX_NO_TOOLS, ensure_ascii=False)]
    for feature in CODEX_OFF:
        cmd += ["--disable", feature]
    return cmd + ["-s", "read-only", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                  "-C", workdir, "--output-schema", schema_file, "-o", out_file, "--json", "-"]


def _under_project(d):
    """True if d or a folder above it holds .git or AGENTS.md: codex walks up from the cwd and puts every AGENTS.md on
    the way into the prompt (4,742 chars of the course repo's rules with the cwd under .market, prompt-input 11/05)."""
    d = os.path.realpath(d)
    while True:
        if any(os.path.exists(os.path.join(d, n)) for n in (".git", "AGENTS.md", "AGENTS.override.md")):
            return True
        up = os.path.dirname(d)
        if up == d:
            return False
        d = up


def codex_workdir(tag, scratch):
    """The -C directory: CODEX_CWD_ROOT/t_<tag>, the same empty folder for every call of a tag, outside any repository,
    since codex writes the cwd into the environment_context message just before the prompt (`codex debug prompt-input`,
    0.166) and a fresh random one made that part differ on every call. Calls of a tag share it at once: the sandbox is
    read-only, every tool is off and --ephemeral writes nothing there. Not empty, under a repository, or no MARKET_DIR:
    the per-call scratch folder, as before, so a decision never sees a file. What it cannot fix: codex sends the new
    session's id as prompt_cache_key (codex-rs core/src/client.rs, PromptCacheKey::SessionDerived; no flag or config
    sets it), so calls of one tag still land on cache shards apart and the hits are mostly the prefix shared by every
    codex call (6,912 of ~9,000 tokens, 11/05). The cached share is measured (llm_calls.jsonl cached_tokens) and
    watched (scorecard cache_drop)."""
    if os.environ.get("MARKET_DIR"):
        d = os.path.join(CODEX_CWD_ROOT, "t_" + re.sub(r"[^A-Za-z0-9_-]", "_", tag))
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
            if not os.listdir(d) and not _under_project(d):
                return d
        except OSError:
            pass
        _codex_measure("llm_codex_cwd", tag, "scratch", 0, d)
    os.makedirs(scratch, exist_ok=True)
    return scratch


class _Slot:
    """One of CODEX_CONCURRENCY lock files in MARKET_DIR, shared by every process of the kit; the lock goes with the
    process, so a killed call frees its slot. Without MARKET_DIR (a test, a demo) there is no limit."""

    def __init__(self, tag, wait=None):
        self.tag, self.fd, self.wait = tag, None, CODEX_QUEUE_WAIT if wait is None else wait

    def __enter__(self):
        md = os.environ.get("MARKET_DIR")
        if not md or CODEX_CONCURRENCY <= 0:
            return self
        d = os.path.join(md, "codex-slots")
        os.makedirs(d, exist_ok=True)
        deadline = time.time() + self.wait
        while True:
            for i in range(CODEX_CONCURRENCY):
                fd = os.open(os.path.join(d, f"{i}.lock"), os.O_RDWR | os.O_CREAT, 0o644)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self.fd = fd
                    return self
                except OSError:
                    os.close(fd)
            if time.time() >= deadline:
                _codex_measure("llm_codex_fail", self.tag, "busy", 0)
                raise HarnessError(f"codex busy: {CODEX_CONCURRENCY} calls running for {self.wait:.0f} s")
            time.sleep(0.2)

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)       # closing drops the flock


def _codex_measure(name, tag, kind, seconds, detail=""):
    if os.environ.get("MARKET_DIR"):
        try:
            import measure
            measure.write(os.environ["MARKET_DIR"], name, {"tag": tag, "pid": os.getpid(), "kind": kind,
                                                           "seconds": round(seconds, 2), "detail": detail[:200]})
        except Exception:   # noqa: BLE001 - the caller expects the HarnessError, not a measurement error
            pass


CODEX_ANSWERING = ("agent_message", "reasoning", "error", "todo_list")   # every other item type is an action


def _codex_events(stdout):
    """(last agent message, usage, error messages, failed turn, actions taken) from the --json event lines. An action is
    anything besides answering: with every tool off there should be none, so one means something was left on."""
    text, usage, errors, failed, actions = None, {}, [], False, []
    for line in stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        item = ev.get("item") or {}
        if item.get("type") and item["type"] not in CODEX_ANSWERING:
            actions.append(":".join(str(item[k]) for k in ("type", "server", "tool", "name") if item.get(k)))
        if ev.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text")
        elif ev.get("type") == "turn.completed":
            u = ev.get("usage") or {}
            usage = {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens"),
                     "cached_tokens": u.get("cached_input_tokens")}
        elif ev.get("type") in ("error", "turn.failed"):
            failed = failed or ev["type"] == "turn.failed"
            errors.append(ev.get("message") or (ev.get("error") or {}).get("message") or "")
    return text, usage, [e for e in errors if e], failed, actions


def _run_group(cmd, prompt, cwd, env, timeout=None):
    """subprocess.run in its own process group: on a timeout the whole group goes, since the npm `codex` is node
    starting the native binary as a child, which killing node alone would leave running."""
    with subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                          encoding="utf-8", errors="replace", cwd=cwd, env=env, start_new_session=True) as p:
        try:
            out, err = p.communicate(prompt, timeout=timeout or CODEX_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            p.communicate()
            raise
        return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def _ask_codex(prompt, schema, system, tag, timeout=None):
    """Any error, a full disk or an unreadable slot folder included, reaches the caller as HarnessError: the callers catch
    only that one to fall back to their rules."""
    try:
        return _codex_once(prompt, schema, system, tag, timeout)
    except HarnessError:
        raise
    except Exception as e:   # noqa: BLE001 - see above
        _codex_measure("llm_codex_fail", tag, "error", 0, f"{type(e).__name__}: {e}")
        raise HarnessError(f"codex call failed: {type(e).__name__}: {e}") from e


def _codex_once(prompt, schema, system, tag, timeout=None):
    codex = find_codex()
    limit = timeout or CODEX_TIMEOUT
    env = {k: os.environ[k] for k in CODEX_ENV if k in os.environ}
    last = None
    first = time.time()
    with _Slot(tag, min(CODEX_QUEUE_WAIT, limit) if timeout else None):
        for attempt in range(1, RETRIES + 1):
            if attempt > 1 and time.time() - first > limit / 2:
                break           # a slow failure is not tried again: the counterpart is waiting
            started = time.time()
            with tempfile.TemporaryDirectory(prefix="harness-codex-") as empty:
                work = codex_workdir(tag, os.path.join(empty, "work"))
                schema_file, out_file = os.path.join(empty, "schema.json"), os.path.join(empty, "answer.json")
                with open(schema_file, "w", encoding="utf-8") as f:
                    json.dump(schema, f)
                try:
                    proc = _run_group(codex_cmd(codex, schema_file, out_file, work, system), prompt, work, env, limit)
                except subprocess.TimeoutExpired:
                    last = f"timeout after {limit}s"
                    log(tag, f"attempt {attempt}: {last}")
                    record_call(tag, prompt, system, time.time() - started, False)
                    _codex_measure("llm_codex_fail", tag, "timeout", time.time() - started)
                    break       # a second wait as long would leave the counterpart waiting twice
                except OSError as e:
                    raise HarnessError(f"codex did not start: {e}") from e
                took = time.time() - started
                text, usage, errors, failed, actions = _codex_events(proc.stdout)
                if os.path.exists(out_file):
                    with open(out_file, encoding="utf-8", errors="replace") as f:
                        text = f.read() or text
            if actions:
                last = f"codex took actions {sorted(set(actions))} with every tool off"
                log(tag, f"attempt {attempt}: {last}")
                record_call(tag, prompt, system, took, False, usage)
                _codex_measure("llm_codex_fail", tag, "action", took, last)
                break           # a setting let a tool through: no answer from that run is used, and no second run
            # an error event before a good answer (a reconnect) is no failure; a failed turn always is
            if proc.returncode or failed or (errors and not text):
                last = f"codex exit {proc.returncode}: {('; '.join(errors) or proc.stderr.strip())[-300:]}"
                log(tag, f"attempt {attempt}: {last}")
                record_call(tag, prompt, system, took, False, usage)
                final = bool(CODEX_FINAL.search(" ".join(errors) or proc.stderr[-500:]))
                _codex_measure("llm_codex_fail", tag, "refused" if final else "exit", took, last)
                if final:
                    break
                continue
            try:
                answer = validate(json.loads(text or ""), schema)
            except (ValueError, HarnessError) as e:
                last = f"invalid answer: {e}"
                log(tag, f"attempt {attempt}: {last}")
                record_call(tag, prompt, system, took, False, usage)
                _codex_measure("llm_codex_fail", tag, "invalid", took, last)
                continue
            record_call(tag, prompt, system, took, True, usage)
            log(tag, f"codex {CODEX_MODEL} effort={CODEX_REASONING} output-schema {took:.1f}s "
                     f"-> {json.dumps(answer, ensure_ascii=False)}")
            return answer
    raise HarnessError(f"no valid answer after {attempt} attempt(s): {last}")


def _ask_claude(prompt, schema, system, tag):
    cmd = BASE_CMD + ["--system-prompt", system, "--json-schema", json.dumps(schema)]
    last = None
    for attempt in range(1, RETRIES + 1):
        started = time.time()
        with tempfile.TemporaryDirectory(prefix="harness-") as empty:
            try:
                proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                      cwd=empty, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                last = f"timeout after {TIMEOUT}s"
                log(tag, f"attempt {attempt}: {last}")
                continue
        took = time.time() - started
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last = f"exit {proc.returncode}, stdout not JSON: {proc.stdout[:200]!r} {proc.stderr[:200]!r}"
            log(tag, f"attempt {attempt}: {last}")
            continue
        if out.get("is_error"):
            last = f"harness error: {out.get('result')!r}"
            log(tag, f"attempt {attempt}: {last}")
            continue
        try:
            answer = validate(out.get("structured_output"), schema)
        except HarnessError as e:
            last = str(e)
            log(tag, f"attempt {attempt}: invalid answer: {last}")
            continue
        log(tag, f"claude -p --model {MODEL} --json-schema(set) {took:.1f}s -> {json.dumps(answer, ensure_ascii=False)}")
        return answer
    raise HarnessError(f"no valid answer after {RETRIES} attempts: {last}")


if __name__ == "__main__":
    demo = {"type": "object", "additionalProperties": False, "required": ["action", "price"],
            "properties": {"action": {"type": "string", "enum": ["counter", "accept", "reject"]},
                           "price": {"type": "integer"}}}
    print(ask("Lamp, reserve 100. The buyer offered 80. What do you do?", demo, "You are a shop owner."))
