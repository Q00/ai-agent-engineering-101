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
                "--model",
                config["model"],
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output),
                "-",
            ]
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
                '{"disableAllHooks":true}',
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
        result = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            cwd=temp,
            timeout=config.get("timeout", 600),
            check=False,
        )
        if result.returncode:
            raise ValueError(
                f"{kind} exited with {result.returncode}; no retry was made"
            )
        if kind == "codex":
            if not output.is_file():
                raise ValueError("Codex did not produce a final response file")
            return output.read_text(), {"cli_version": version, "transport": "cli"}
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
