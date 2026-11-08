"""Reversible CLI profiles using existing Claude/Codex logins.

--check makes no model call. --preflight probes active Opus/GPT models.
--profile sol|opus5.5|haiku|astra changes the trading profile, not a service.
--advisor-profile haiku|luna changes only the sales-strategy analyst's profile.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
CODEX_MODELS = {"sol": "gpt-6.1-sol", "astra": "gpt-6-astra", "luna": "gpt-6-luna"}
CLAUDE_MODELS = {"haiku": "haiku", "opus5.5": "claude-opus-5-5"}
CLAUDE_COMMAND = ["claude", "-p", "--model", "haiku", "--tools", "",
                  "--setting-sources", "", "--no-session-persistence",
                  "--strict-mcp-config", "--disable-slash-commands"]
DISABLED_FEATURES = ("shell_tool", "view_image", "apps", "plugins", "hooks", "multi_agent",
                     "browser_use", "computer_use", "image_generation", "code_mode",
                     "code_mode_host", "memories", "shell_snapshot")


def load_profile(root=ROOT):
    path = Path(root) / "model-profile.json"
    if not path.exists():
        return {"version": 1, "trading": "haiku", "advisor": "haiku"}
    if path.is_symlink() or path.stat().st_size > 4096:
        raise ValueError("Invalid model profile file")
    cfg = json.loads(path.read_text())
    if (not isinstance(cfg, dict) or set(cfg) != {"version", "trading", "advisor"}
            or type(cfg["version"]) is not int or cfg["version"] != 1
            or cfg["trading"] not in ("sol", "astra", *CLAUDE_MODELS) or cfg["advisor"] not in ("luna", "haiku")):
        raise ValueError("Expected trading sol/opus5.5/astra/haiku and advisor luna/haiku")
    return cfg


def required_executables(root=ROOT):
    cfg = load_profile(root)
    profiles = active_profiles(root, cfg)
    return (("claude",) if any(profile in CLAUDE_MODELS for profile in profiles) else ()) + (
        ("codex",) if any(profile in CODEX_MODELS for profile in profiles) else ())


def for_prompt(prompt_name, root=ROOT):
    cfg = load_profile(root)
    if prompt_name == "sales_advisor.txt" and not advisor_enabled(root):
        raise ValueError("Sales advisor is disabled")
    return cfg["advisor"] if prompt_name == "sales_advisor.txt" else cfg["trading"]


def advisor_enabled(root=ROOT):
    """Archived advisor profiles are inactive unless explicitly enabled.

    Read only the shared enable flag here to avoid importing the advisor's
    model harness back into its own backend. Full advisor validation stays in
    sales_advisor.config_at when that optional process starts.
    """
    path = Path(root) / "advisor-config.json"
    if not path.exists():
        return False
    if path.is_symlink() or path.stat().st_size > 4096:
        raise ValueError("Invalid advisor configuration")
    cfg = json.loads(path.read_text())
    if not isinstance(cfg, dict) or type(cfg.get("enabled")) is not bool:
        raise ValueError("Advisor configuration needs an explicit enabled flag")
    return cfg["enabled"]


def active_profiles(root=ROOT, cfg=None):
    cfg = load_profile(root) if cfg is None else cfg
    return (cfg["trading"], cfg["advisor"]) if advisor_enabled(root) else (cfg["trading"],)


def environment(profile):
    env = dict(os.environ)
    if profile in CODEX_MODELS:
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            env.pop(key, None)
    return env


def execute(command, *, prompt=None, cwd=None, timeout=90, env=None):
    """Stop the CLI and its children on timeout; never leave a model retry alive."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, cwd=cwd, env=env,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        raise
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def ensure_chatgpt(*, timeout=8, cwd=None):
    # Inspect the mode before enforcing it: a mismatched forced login method
    # can sign a user out. Never read or print their credential file.
    result = execute(["codex", "login", "status"], timeout=timeout,
                     cwd=cwd, env=environment("astra"))
    status = (result.stdout + "\n" + result.stderr).lower()
    if result.returncode or "logged in using chatgpt" not in status or "api key" in status:
        raise ValueError("Codex needs ChatGPT login on this server: codex login --device-auth")


def schema_for(prompt_name):
    props = {"reason": {"type": "string"}}
    if prompt_name == "sales_advisor.txt":
        # Mirrors sales_advisor.validate_advice. Runtime validation still checks
        # evidence IDs and categories against this observation, not model claims.
        fields = {
            "price_bias_percent": {"type": "integer", "minimum": -5, "maximum": 5},
            "pace": {"type": "string", "enum": ["balanced", "quick"]},
            "focus": {"type": "string", "enum": ["value", "clarity", "quality"]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 4},
        }
        category_fields = {"category": {"type": "string"}, **fields}
        props = {
            "reason": {"type": "string", "maxLength": 600},
            "default": {"type": "object", "properties": fields,
                        "required": list(fields), "additionalProperties": False},
            "categories": {"type": "array", "maxItems": 4, "items": {
                "type": "object", "properties": category_fields,
                "required": list(category_fields), "additionalProperties": False}},
        }
    elif prompt_name == "preflight":
        props["ready"] = {"type": "boolean"}
    elif prompt_name == "market_select.txt":
        props.update(name={"type": "string"}, sku={"type": ["string", "null"]})
    elif prompt_name.startswith("seller_opening"):
        props["price"] = {"type": "integer"}
    else:
        props.update(action={"type": "string"}, price={"type": ["integer", "null"]})
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def command(profile, folder, prompt_name):
    if profile in CLAUDE_MODELS:
        args = list(CLAUDE_COMMAND)
        args[args.index("--model") + 1] = CLAUDE_MODELS[profile]
        if profile == "opus5.5":
            # Pin the requested model rather than the floating 'opus' alias.
            # JSON is the CLI result envelope, not --json-schema, which can
            # require an additional structured-output tool/model turn.
            args.extend(["--effort", "low", "--output-format", "json"])
        return args
    if profile not in CODEX_MODELS:
        raise ValueError("Unknown model profile")
    folder = Path(folder)
    (folder / "schema.json").write_text(json.dumps(schema_for(prompt_name)))
    args = ["codex", "exec", "--ignore-user-config", "--ignore-rules", "--ephemeral",
            "--skip-git-repo-check", "--sandbox", "read-only", "--model", CODEX_MODELS[profile],
            "--color", "never", "--json", "--output-schema", str(folder / "schema.json"),
            "--output-last-message", str(folder / "answer.json")]
    settings = ('approval_policy="never"', 'forced_login_method="chatgpt"',
                'model_provider="openai"', 'model_reasoning_effort="low"',
                'web_search="disabled"', 'project_doc_max_bytes=0',
                'skills.include_instructions=false', 'skills.bundled.enabled=false')
    for setting in settings:
        args.extend(["--config", setting])
    for feature in DISABLED_FEATURES:
        args.extend(["--disable", feature])
    return args + ["-"]


def answer(profile, result, folder):
    if profile == "haiku":
        return result.stdout
    if profile == "opus5.5":
        if not isinstance(result.stdout, str) or len(result.stdout.encode()) > 65536:
            raise ValueError("Claude did not produce a bounded JSON result")
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ValueError("Claude emitted an invalid JSON result") from exc
        if (not isinstance(output, dict) or output.get("type") != "result"
                or output.get("subtype") != "success" or output.get("is_error") is not False
                or type(output.get("num_turns")) is not int or output["num_turns"] != 1
                or output.get("permission_denials") != [] or output.get("deferred_tool_use") is not None):
            raise ValueError("Claude did not complete one successful tool-free decision")
        model, usage = CLAUDE_MODELS[profile], output.get("modelUsage")
        if (not isinstance(usage, dict) or set(usage) != {model}
                or not isinstance(usage[model], dict)
                or type(usage[model].get("outputTokens")) is not int or usage[model]["outputTokens"] <= 0
                or usage[model].get("canonicalModel", model) != model):
            raise ValueError("Claude result did not verify the exact requested claude-opus-5-5 model; no fallback")
        text = output.get("result")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Claude returned no decision text")
        return text
    # --json is a lifecycle stream, not the answer. Unexpected tools or failed
    # turns are rejected, even if a plausible final decision was also written.
    complete = False
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError("Codex emitted an invalid event stream") from exc
        if not isinstance(event, dict):
            raise ValueError("Codex emitted a non-object event")
        kind = event.get("type")
        if kind == "turn.completed":
            complete = True
        elif kind in ("error", "turn.failed"):
            raise ValueError("Codex reported a failed turn")
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict) or item.get("type") not in ("agent_message", "reasoning"):
                raise ValueError("Codex attempted a tool instead of a model decision")
        elif kind not in ("thread.started", "turn.started"):
            raise ValueError("Codex emitted an unsupported event type")
    path = Path(folder) / "answer.json"
    if not complete or path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise ValueError("Codex did not produce a bounded final decision")
    return path.read_text()


def call(profile, prompt_name, prompt, *, timeout, log):
    deadline = time.monotonic() + timeout
    with tempfile.TemporaryDirectory(prefix="week08-model-") as folder:
        if profile in CODEX_MODELS:
            ensure_chatgpt(timeout=min(8, timeout), cwd=folder)
        cmd = command(profile, folder, prompt_name)
        log.write(f"COMMAND: {shlex.join(cmd)}\nPROFILE: {profile}\nPROMPT:\n{prompt}\n")
        log.flush()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Model deadline expired during authentication check")
        try:
            result = execute(cmd, prompt=prompt, cwd=folder, timeout=remaining, env=environment(profile))
        except subprocess.TimeoutExpired as exc:
            log.write("TIMEOUT: model process and children stopped\n")
            for label, content in (("STDOUT", exc.stdout), ("STDERR", exc.stderr)):
                if isinstance(content, bytes):
                    content = content.decode("utf-8", errors="replace")
                log.write(f"{label}:\n{content or ''}\n")
            log.flush()
            raise
        log.write(f"EXIT: {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\n")
        if result.returncode:
            raise ValueError(f"{profile} CLI failed (exit {result.returncode}); no model fallback")
        text = answer(profile, result, folder)
        if profile in CODEX_MODELS or profile == "opus5.5":
            log.write("FINAL ANSWER:\n" + text + "\n")
        return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--profile", choices=("sol", "opus5.5", "astra", "haiku"))
    mode.add_argument("--advisor-profile", choices=("luna", "haiku"))
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        cfg = load_profile(ROOT)
        if args.profile or args.advisor_profile:
            if args.profile:
                cfg["trading"] = args.profile
            else:
                cfg["advisor"] = args.advisor_profile
            from feedback import atomic_json
            atomic_json(ROOT / "model-profile.json", cfg)
            print(f"Local profile saved: trading={cfg['trading']}, advisor={cfg['advisor']}. Deploy/restart to apply.")
            return 0
        active_advisor = cfg["advisor"] if advisor_enabled(ROOT) else "disabled"
        print(f"Model profile: trading={cfg['trading']}, advisor={active_advisor}")
        for executable in required_executables(ROOT):
            if shutil.which(executable) is None:
                raise ValueError(f"{executable} not found in service PATH; install it before deploying")
        selected = [profile for profile in (*CODEX_MODELS, "opus5.5") if profile in active_profiles(ROOT, cfg)]
        if selected:
            if args.preflight:
                logs = ROOT / "logs"
                logs.mkdir(exist_ok=True)
                path = logs / f"model-preflight-{uuid.uuid4().hex}.log"
                with path.open("x") as log:
                    print(f"Model probe details: {path}", flush=True)
                    try:
                        for profile in selected:
                            raw = call(profile, "preflight", 'Return only {"ready":true,"reason":"ok"}. Do not use tools.',
                                       timeout=60, log=log)
                            result = json.loads(raw)
                            if (not isinstance(result, dict) or result.get("ready") is not True
                                    or not isinstance(result.get("reason"), str)):
                                raise ValueError(f"{profile} preflight response did not match its contract")
                            model = CLAUDE_MODELS[profile] if profile in CLAUDE_MODELS else CODEX_MODELS[profile]
                            print(f"{model} low: authenticated decision probe passed; no market trade made.")
                    except Exception as exc:
                        log.write(f"ERROR: {type(exc).__name__}: {exc}\n")
                        raise
            else:
                if any(profile in CODEX_MODELS for profile in selected):
                    ensure_chatgpt()
                    print("ChatGPT login confirmed; model availability not probed.")
                if "opus5.5" in selected:
                    print("Claude Opus 5.5 access is not probed by --check; use --preflight before deploying.")
        print("Model preflight complete." if args.preflight else "Readiness check complete; no model call.")
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"Model configuration not ready: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
