"""Small interchangeable CLI and HTTP model transports; no agent framework."""

import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .review import prompt_for, validate_review
from .storage import canonical, digest, write_new


class ProviderFailure(ValueError):
    """A safe transport category and metadata, never a provider error body."""

    def __init__(self, category, metadata=None):
        self.category = category
        self.metadata = metadata or {}
        super().__init__(category)


class ProviderUnavailable(ProviderFailure):
    """Safe provider failure category without response bodies or credentials."""


CODEX_ISOLATION = (
    "unified_exec",
    "apps",
    "plugins",
    "remote_plugin",
    "memories",
    "hooks",
    "browser_use",
    "computer_use",
    "image_generation",
    "view_image",
    "goals",
    "sleep_tool",
)


def codex_failure(events, metadata):
    errors = [e for e in events if e.get("type") in {"error", "turn.failed"}]
    messages = " ".join(
        str(e.get("error", e.get("message", ""))) for e in errors
    ).lower()
    for terms, category, unavailable in (
        (("usage limit", "session limit", "usagelimitexceeded"), "usage-limit", True),
        (("rate limit", "too many requests"), "rate-limit", True),
        (("unauthorized", "authentication"), "authentication", True),
        (
            ("contextwindowexceeded", "context length", "context window"),
            "context-limit",
            False,
        ),
        (
            ("invalid schema", "json_schema", "response_format"),
            "schema-rejected",
            False,
        ),
        (("connection", "stream disconnected", "overloaded"), "connection", False),
    ):
        if any(term in messages for term in terms):
            return (ProviderUnavailable if unavailable else ProviderFailure)(
                category, metadata
            )
    return ProviderFailure("cli-error", metadata)


def parse_json_text(text):
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("```"):
        text = text[8:-3].strip()
    elif text.startswith("```\n") and text.endswith("```"):
        text = text[4:-3].strip()
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("Model response must be a JSON object")
    return result


def validate_config(config):
    allowed = {
        "kind",
        "model",
        "base_url",
        "api_key_env",
        "parameters",
        "timeout",
        "structured_output",
        "effort",
    }
    if set(config) - allowed:
        raise ValueError(
            "Unknown provider setting; store API keys in environment variables only"
        )
    if config.get("kind") not in {"codex", "claude", "openai", "anthropic"}:
        raise ValueError("Provider kind must be codex, claude, openai or anthropic")
    if (
        not isinstance(config.get("model"), str)
        or not config["model"].strip()
        or config["model"].startswith("YOUR_")
    ):
        raise ValueError("Set an explicit model in the provider configuration")
    params = config.get("parameters", {})
    if not isinstance(params, dict) or set(params) & {
        "model",
        "messages",
        "tools",
        "tool_choice",
        "stream",
        "response_format",
        "system",
    }:
        raise ValueError("parameters cannot override the evaluation protocol")
    if config["kind"] in {"codex", "claude"} and params:
        raise ValueError("CLI adapters do not accept API parameters")
    if config.get("timeout", 600) <= 0:
        raise ValueError("timeout must be positive")
    if "effort" in config and (
        config["kind"] not in {"claude", "codex"}
        or config["effort"] not in {"low", "medium", "high"}
    ):
        raise ValueError("effort is supported only for CLI: low/medium/high")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError(
            "Refusing API redirect; configure the final endpoint explicitly"
        )


def http_review(config, prompt, schema):
    kind = config["kind"]
    base = config.get(
        "base_url",
        "https://api.openai.com/v1"
        if kind == "openai"
        else "https://api.anthropic.com/v1",
    ).rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("API URL must not contain credentials, query or fragment")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("Use HTTPS, or HTTP on a loopback local model server")
    key_name = config.get(
        "api_key_env", "OPENAI_API_KEY" if kind == "openai" else "ANTHROPIC_API_KEY"
    )
    key = os.environ.get(key_name) if key_name else None
    if key_name and not key:
        raise ValueError(
            f"Required API key environment variable is not set: {key_name}"
        )
    headers = {"Content-Type": "application/json"}
    payload = {
        "model": config["model"],
        "messages": [{"role": "user", "content": prompt}],
        **config.get("parameters", {}),
    }
    if kind == "openai":
        endpoint = base + "/chat/completions"
        if key:
            headers["Authorization"] = "Bearer " + key
        if config.get("structured_output", False):
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "assignment_review",
                    "strict": True,
                    "schema": schema,
                },
            }
    else:
        endpoint = base + "/messages"
        headers["anthropic-version"] = "2023-06-01"
        if key:
            headers["x-api-key"] = key
        payload.setdefault("max_tokens", 8192)
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.build_opener(NoRedirect()).open(
            request, timeout=config.get("timeout", 600)
        ) as response:
            raw = response.read().decode()
    except urllib.error.HTTPError as error:
        # API errors may echo keys or submitted text. Do not display their bodies.
        raise ValueError(
            f"Model endpoint returned HTTP {error.code}; no retry was made"
        ) from None
    data = json.loads(raw)
    if kind == "openai":
        text = data["choices"][0]["message"]["content"]
    else:
        text = "\n".join(
            block["text"] for block in data["content"] if block["type"] == "text"
        )
    return text, {
        "response_model": data.get("model"),
        "usage": data.get("usage"),
        "transport": "http",
    }


def cli_review(config, prompt, schema):
    """Run in an empty directory with student instructions and tools disabled."""
    kind = config["kind"]
    with tempfile.TemporaryDirectory(prefix="ax-judge-") as temp:
        root = Path(temp)
        schema_path, output = root / "schema.json", root / "response.json"
        schema_path.write_text(canonical(schema), encoding="utf-8")
        if kind == "codex":
            args = [
                "codex",
                "exec",
                "--ignore-user-config",
                "--ephemeral",
                "--json",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--disable",
                "shell_tool",
                "--disable",
                "code_mode",
                "--disable",
                "code_mode_host",
                "--disable",
                "multi_agent",
                "--disable",
                "skill_search",
                "--enable",
                "skip_host_skill_discovery",
                "-c",
                'web_search="disabled"',
                "-c",
                'approval_policy="never"',
                "-c",
                "project_doc_max_bytes=0",
                "--model",
                config["model"],
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output),
                "-",
            ]
            for feature in CODEX_ISOLATION:
                args.extend(["--disable", feature])
        else:
            args = [
                "claude",
                "-p",
                "--model",
                config["model"],
                "--restricted",
                "--tools",
                "",
                "--disable-slash-commands",
                "--no-session-persistence",
                "--setting-sources",
                "",
                "--settings",
                '{"disableAllHooks":true,"claudeMdExcludes":["**"],"autoMemoryEnabled":false}',
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--permission-mode",
                "dontAsk",
                "--output-format",
                "json",
                "--json-schema",
                canonical(schema),
                "--system-prompt",
                "You are a course submission reviewer. "
                "Use only supplied evidence. No tool use.",
            ]
        version = subprocess.run(
            [kind, "--version"], capture_output=True, text=True, timeout=15, check=True
        ).stdout.strip()
        if "effort" in config:
            args += (
                ["--effort", config["effort"]]
                if kind == "claude"
                else ["-c", 'model_reasoning_effort="' + config["effort"] + '"']
            )
        result = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=temp,
            timeout=config.get("timeout", 600),
            check=False,
        )
        if kind == "codex":
            try:
                events = [
                    json.loads(line)
                    for line in result.stdout.splitlines()
                    if line.strip()
                ]
                if any(not isinstance(e, dict) for e in events):
                    raise ValueError("Non-object CLI event")
            except ValueError:
                raise ProviderFailure("invalid-cli-events") from None
            totals = {}
            for event in events:
                if event.get("type") == "turn.completed":
                    for key, value in event.get("usage", {}).items():
                        if isinstance(value, (int, float)):
                            totals[key] = totals.get(key, 0) + value
            metadata = {
                "cli_version": version,
                "transport": "cli",
                "usage": totals or None,
                "requested_model": config["model"],
                "response_models": sorted(
                    {e["model"] for e in events if isinstance(e.get("model"), str)}
                ),
                "event_types": sorted({e["type"] for e in events if "type" in e}),
                "item_types": sorted(
                    {
                        e["item"]["type"]
                        for e in events
                        if isinstance(e.get("item"), dict) and "type" in e["item"]
                    }
                ),
                "returncode": result.returncode,
                "project_doc_max_bytes": 0,
                "disabled_features": sorted(
                    {
                        "shell_tool",
                        "code_mode",
                        "code_mode_host",
                        "multi_agent",
                        "skill_search",
                        *CODEX_ISOLATION,
                    }
                ),
            }
            if set(metadata["item_types"]) - {"agent_message", "reasoning", "error"}:
                raise ProviderFailure("prohibited-tool-action", metadata)
            if result.returncode or any(e.get("type") == "turn.failed" for e in events):
                raise codex_failure(events, metadata)
            notices = [
                e["item"]
                for e in events
                if isinstance(e.get("item"), dict) and e["item"].get("type") == "error"
            ]
            if any(
                not str(i.get("message", "")).startswith(
                    (
                        "Under-development features enabled:",
                        "Code Mode is unavailable because code-mode host is disabled.",
                    )
                )
                for i in notices
            ):
                raise ProviderFailure("cli-notice-error", metadata)
            metadata["nonfatal_notice_count"] = len(notices)
            if not output.is_file() or not any(
                e.get("type") == "turn.completed" for e in events
            ):
                raise ProviderFailure("missing-completed-response", metadata)
            return output.read_text(), metadata
        if result.returncode:
            try:
                envelope = json.loads(result.stdout)
            except ValueError:
                envelope = {}
            message = str(envelope.get("result", "")).lower()
            if "session limit" in message or "usage limit" in message:
                raise ProviderUnavailable("usage-limit")
            raise ValueError(
                f"{kind} exited with {result.returncode}; no retry was made"
            )
        envelope = json.loads(result.stdout)
        if envelope.get("is_error"):
            raise ValueError("Claude returned an error result; no retry was made")
        text = (
            canonical(envelope["structured_output"])
            if envelope.get("structured_output")
            else envelope.get("result", "")
        )
        return text, {
            "cli_version": version,
            "transport": "cli",
            "usage": envelope.get("usage"),
            "response_models": sorted(envelope.get("modelUsage", {})),
            "model_usage": envelope.get("modelUsage"),
            "reported_cost_usd": envelope.get("total_cost_usd"),
        }


def judge(bundle, snapshot, config, out):
    out = Path(out)
    if out.exists():
        raise ValueError("Choose a new model output directory")
    validate_config(config)
    prompt = prompt_for(bundle)
    # Save provenance BEFORE dispatch, including malformed/failed model attempts.
    write_new(
        out / "request.json",
        {
            "bundle_id": bundle["id"],
            "config": config,
            "config_id": digest(config),
            "prompt_id": digest(prompt),
        },
    )
    try:
        transport = cli_review if config["kind"] in {"codex", "claude"} else http_review
        text, metadata = transport(config, prompt, bundle["response_schema"])
        write_new(out / "raw-response.txt", text)
        write_new(out / "provider-metadata.json", metadata)
        review = parse_json_text(text)
        validate_review(review, bundle, snapshot)
        response = {
            "bundle_id": bundle["id"],
            "review": review,
            "provenance": {
                "kind": config["kind"],
                "model": config["model"],
                "config_id": digest(config),
                **metadata,
            },
        }
        write_new(out / "response.json", response)
        return response
    except Exception as error:
        # Raw responses remain available for malformed JSON; error bodies stay private.
        write_new(
            out / "failure.json",
            {"error_type": type(error).__name__, "status": "no-score-produced"},
        )
        raise
