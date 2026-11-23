"""codex_client v4: one model call through the codex CLI for the seller hook's price-parse assist.
Safety checks:
  * model check uses _MODEL_RE.fullmatch (no trailing newline slips through "$").
  * counter_path / call_log_path are PINNED (COUNTER_REL / CALL_LOG_REL under the candidate dir). A config that
    sets a different value is refused before anything is counted (changing it used to restart the cap).
  * cli must be an absolute path on ALLOWED_CLI (compared by realpath), a regular executable file not writable by
    group/others; PATH lookup is gone. The child env is minimal (HOME, PATH=/usr/bin:/bin, LANG/LC_ALL, and
    CODEX_HOME only if set), not the whole parent environment.

Real-call adapter (code only; NOT exercised against a real model: 0 real calls. Codex CLI 0.166.0 is installed
on the box since 17:15 KST, login is pending, real_calls_enabled is false).
Same path as the course starter harness (upstream/scripts/starter_round3/harness.py, round3_strategy.md L124):
    <cli> exec --ephemeral --skip-git-repo-check --sandbox read-only --ignore-user-config -C <empty tmp dir>
        <TOOL_OFF_ARGS> -m <model> -c model_reasoning_effort=<effort> -
(cli path, model and effort come from llm_config.json; default model gpt-6.1-terra; extra_args must be [] -
 v1.17b: any non-empty extra_args is refused, model / effort are pattern-checked)
prompt on stdin, answer on stdout, cwd = -C = an empty temporary directory, hard timeout (subprocess.run timeout).
--sandbox read-only, --ephemeral, -C and the empty cwd are FIXED here (not settings).
File-read blocking (QA M10-4): --sandbox read-only does NOT stop the shell tool from READING files. TOOL_OFF_ARGS turns
the model's tools off by feature flag (--disable shell_tool / unified_exec / apps / plugins / ... as listed by
`codex features list` of 0.166.0) and sets shell_environment_policy.inherit=none. That these flags really stop all
tool use / file reads is [미확인]: verified only that the flag names exist in `codex exec --help` / `codex features
list`; no real run.

Safety:
  * real_calls_enabled must be true in llm_config.json (default false), else RealCallsDisabled.
  * the CLI must be on PATH (shutil.which), else CodexUnavailable. No install, no login, no API key handling here.
  * HARD COUNTER (persistent file, counted before the subprocess starts): TOTAL_CAP = 20 real calls in all,
    split EDITOR_CAP 6 / PARSE_CAP 10 / RESERVE_CAP 4 (a role over its share may draw on the reserve; nothing
    passes 20). Over the cap -> CallCapReached. These are constants, not settings.
  * call log (JSONL): ts, role, model, prompt sha256 + byte length, status, latency, output sha256 + length.
    The prompt text and the output text are NOT logged; no environment value is logged.
Every exception here is caught by llm.client.call_with_timeout -> the caller takes the rule path."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from llm.filelock import atomic_write_json, locked

HERE = Path(__file__).resolve().parent.parent
CONFIG = HERE / "llm_config.json"
TOTAL_CAP, EDITOR_CAP, PARSE_CAP, RESERVE_CAP = 20, 6, 10, 4
FIXED_ARGS = ("--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--ignore-user-config")
TOOL_OFF_FEATURES = ("shell_tool", "unified_exec", "shell_snapshot", "apps", "plugins", "remote_plugin",
                     "browser_use", "browser_use_external", "in_app_browser", "computer_use", "view_image",
                     "skill_search")
TOOL_OFF_ARGS = tuple(a for f in TOOL_OFF_FEATURES for a in ("--disable", f)) + (
    "-c", "shell_environment_policy.inherit=none", "-c", 'web_search="disabled"')     # effect [미확인]
ROLE_CAP = {"editor": EDITOR_CAP, "parse": PARSE_CAP}

assert EDITOR_CAP + PARSE_CAP + RESERVE_CAP == TOTAL_CAP


class RealCallsDisabled(RuntimeError):
    pass


class CodexUnavailable(RuntimeError):
    pass


class CallCapReached(RuntimeError):
    pass


def load_config(path=CONFIG):
    c = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(c.get("model"), str) or not c["model"]:
        raise ValueError("llm_config.json: model must be a non-empty string")
    return c


class HardCounter:
    """Persistent real-call counter. Unreadable state = full (fail closed). v1.17 fix M10-1: take() reads, checks and
    writes under llm.filelock.locked (thread lock + fcntl.flock LOCK_EX), single-host premise."""

    def __init__(self, path):
        self.path = Path(path)

    def read(self):
        if not self.path.exists():
            return {"editor": 0, "parse": 0, "reserve": 0}
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return {k: int(d[k]) for k in ("editor", "parse", "reserve")}
        except Exception:                                    # noqa: BLE001
            return {"editor": TOTAL_CAP, "parse": 0, "reserve": RESERVE_CAP}

    def total(self):
        with locked(self.path):
            return sum(self.read().values())

    def take(self, role):
        if role not in ROLE_CAP:
            raise CallCapReached(f"unknown role {role!r}")
        with locked(self.path):
            return self._take(role)

    def _take(self, role):
        d = self.read()
        if sum(d.values()) >= TOTAL_CAP:
            raise CallCapReached(f"total {TOTAL_CAP} reached")
        if d[role] < ROLE_CAP[role]:
            d[role] += 1
        elif d["reserve"] < RESERVE_CAP:
            d["reserve"] += 1
        else:
            raise CallCapReached(f"{role} share {ROLE_CAP[role]} and reserve {RESERVE_CAP} used")
        atomic_write_json(self.path, d)
        return d


# v1.17b fix (CR condition 1): extra_args is NOT a filter any more. Any non-empty extra_args is refused (a prefix
# block list was bypassable: -c sandbox_mode=..., --config, -c approval_policy=..., --enable shell_tool, -p/--profile,
# --search, -c shell_environment_policy...). model and reasoning_effort are the only settings that reach argv and
# both are checked against strict patterns so they cannot carry a flag or a config override either.
REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")
_MODEL_RE = __import__("re").compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,63}")      # fixes4: used with fullmatch


def check_settings(cfg):
    extra = cfg.get("extra_args", [])
    if extra is None:
        extra = []
    if not isinstance(extra, list) or extra:
        raise ValueError("llm_config.json extra_args must be an empty list (any extra codex argument is refused)")
    m = cfg.get("model")
    if not isinstance(m, str) or not _MODEL_RE.fullmatch(m):
        raise ValueError("llm_config.json model must match [A-Za-z0-9][A-Za-z0-9._:-]* (no flags, no spaces)")
    eff = cfg.get("reasoning_effort", "medium")
    if eff not in REASONING_EFFORTS:
        raise ValueError(f"llm_config.json reasoning_effort must be one of {REASONING_EFFORTS}")
    return m, eff


def build_cmd(exe, cfg, cwd):
    model, eff = check_settings(cfg)
    return [exe, "exec", *FIXED_ARGS, "-C", str(cwd), *TOOL_OFF_ARGS, "-m", model,
            "-c", f"model_reasoning_effort={eff}", "-"]


# ---- fixes4 pins
COUNTER_REL = "state/real_call_counter.json"
CALL_LOG_REL = "state/real_calls.jsonl"
ALLOWED_CLI = ("/home/box/.local/bin/codex",          # box
               "/home/sungsoo/.local/bin/codex")      # deployment computer (codex-cli 0.164.0, regular file)
LIVE_COUNTER_REL = "state/live_call_counter.json"


class LiveCounter:
    """v120c-B r3: live per-round ceiling = settings caps.<role>.per_round in llm_caps_config_v4.json; default
    UNLIMITED (성수님 14:38, GPT 6.1 Terra) - the count is still kept. The 20-call test HardCounter stays for the test
    runs; live=True uses this one. Unreadable state or bad caps setting = full (fail closed)."""

    def __init__(self, path, caps_path=None):
        self.path = Path(path)
        self.caps_path = None if caps_path is None else Path(caps_path)

    def take(self, role):
        from llm import caps_v4 as CAPS
        if role not in CAPS.ROLES:
            raise CallCapReached(f"unknown role {role!r}")
        try:
            cap = CAPS.role_caps(CAPS.load_config(self.caps_path) if self.caps_path else {})[role]["per_round"]
        except ValueError as e:
            raise CallCapReached(f"caps setting invalid (fail closed): {e}")
        with locked(self.path):
            d = {"editor": 0, "parse": 0}
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    d = {k: int(raw[k]) for k in ("editor", "parse")}
                    # v121: the new roles (rlpo_prop / rlpo_appr / memory) are optional keys, so a v1.20 counter file
                    # (editor + parse only) stays readable; a present but non-int value is unreadable (fail closed).
                    d.update({k: int(raw[k]) for k in CAPS.ROLES if k not in d and k in raw})
                except Exception:                    # noqa: BLE001
                    raise CallCapReached("live counter unreadable (fail closed)")
            d.setdefault(role, 0)
            if cap is not None and d[role] >= cap:
                raise CallCapReached(f"live {role} round cap {cap} reached")
            d[role] += 1
            atomic_write_json(self.path, d)
            return d


def check_pins(cfg):
    for key, pinned in (("counter_path", COUNTER_REL), ("call_log_path", CALL_LOG_REL)):
        v = cfg.get(key, pinned)
        if v != pinned:
            raise ValueError(f"llm_config.json {key} is pinned to {pinned!r} (got {v!r}): refused")


def resolve_cli(cli, allowed=ALLOWED_CLI):
    """-> absolute executable path, or CodexUnavailable. No PATH lookup; allow-list by realpath."""
    import stat as _st
    if not isinstance(cli, str) or not os.path.isabs(cli):
        raise CodexUnavailable("cli must be an absolute path on the allow-list")
    real = os.path.realpath(cli)
    if real not in {os.path.realpath(a) for a in allowed}:
        raise CodexUnavailable(f"cli {cli} is not on the allow-list")
    try:
        st = os.stat(real)
    except OSError as e:
        raise CodexUnavailable(f"cli {cli} missing") from e
    if not _st.S_ISREG(st.st_mode) or not os.access(real, os.X_OK) or st.st_mode & 0o022:
        raise CodexUnavailable(f"cli {cli} is not a regular executable writable only by its owner")
    return real


def child_env():
    env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    for k in ("HOME", "CODEX_HOME"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


class CodexClient:
    def __init__(self, role, config_path=CONFIG, base=HERE, which=shutil.which, runner=subprocess.run,
                 allowed_cli=None, live=False):
        self.role, self.cfg = role, load_config(config_path)
        self.allowed_cli = ALLOWED_CLI if allowed_cli is None else tuple(allowed_cli)
        self.base, self.which, self.runner = Path(base), which, runner
        check_pins(self.cfg)                                  # fixes4: refused before any count
        self.counter = (LiveCounter(self.base / LIVE_COUNTER_REL, self.base / "llm_caps_config_v4.json") if live
                        else HardCounter(self.base / COUNTER_REL))
        self.log_path = self.base / CALL_LOG_REL

    def _log(self, row):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def complete(self, prompt, *, max_tokens, timeout_s):
        if self.cfg.get("real_calls_enabled") is not True:
            raise RealCallsDisabled("real_calls_enabled is not true in llm_config.json")
        exe = resolve_cli(self.cfg.get("cli"), self.allowed_cli)
        check_settings(self.cfg)                             # v1.17b: bad settings refused BEFORE a count is used
        from pii_mask import mask as _pii_mask                   # v121c (CoS 결정 18:48): email / phone / card / RRN masked
        prompt = _pii_mask(prompt)                           # before the prompt leaves the box (and before its sha)
        self.counter.take(self.role)                         # counted before the call goes out
        row = {"ts": round(time.time(), 3), "role": self.role, "model": self.cfg["model"],
               "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
               "prompt_bytes": len(prompt.encode("utf-8")), "max_tokens": max_tokens}
        t0 = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="r3v117-codex-") as cwd:
                cmd = build_cmd(exe, self.cfg, cwd)
                out = self.runner(cmd, input=prompt, capture_output=True, text=True, cwd=cwd, timeout=timeout_s,
                                  env=child_env())
        except subprocess.TimeoutExpired:
            self._log(dict(row, status="timeout", latency_s=round(time.monotonic() - t0, 3)))
            raise
        lat = round(time.monotonic() - t0, 3)
        if out.returncode != 0:
            # r3: the stderr tail goes into the exception ONLY (caps_v4 classifies quota/429/auth -> auto-OFF);
            # the call log keeps the kind, never the text.
            tail = " ".join(((out.stderr or "") + " " + (out.stdout or "")).split())[-300:]
            from llm.caps_v4 import error_kind
            self._log(dict(row, status=f"exit {out.returncode}", kind=error_kind(tail), latency_s=lat))
            raise RuntimeError(f"codex exit {out.returncode}: {tail}")
        text = (out.stdout or "").strip()
        self._log(dict(row, status="ok", latency_s=lat, output_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                       output_bytes=len(text.encode("utf-8"))))
        return text
