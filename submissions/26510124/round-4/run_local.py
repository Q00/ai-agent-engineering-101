#!/usr/bin/env python3
"""check/demo only: neither command calls the live kit's starter, tools or run."""
import argparse
import importlib.metadata
import json
import os
import shutil

from local_support import ROOT, load_kit, verify_vendor


def check():
    print("Official source files verified:", verify_vendor())
    for package in ("a2a-sdk", "uvicorn", "httpx", "eth-account", "coincurve"):
        print(package + ": " + importlib.metadata.version(package))
    kit = load_kit()
    print("Kit:", kit.KIT_VERSION)
    print("Fixed actions:", ", ".join(kit.FIXED))
    agent = json.loads((ROOT / "agent.json").read_text())
    cfg = agent.get("llm", {})
    if cfg.get("backend") == "claude-cli":
        from claude_model import ClaudeModel
        ClaudeModel(agent)
        print("Model configured: Claude CLI / " + cfg["model"])
        for role, selected in agent.get("role_models", {}).items():
            ClaudeModel({"llm": selected})
            print("Role:", role, "/", selected["model"])
        print("Claude installed:", bool(shutil.which("claude")))
        print("cloudflared installed:", bool(shutil.which("cloudflared")))
        print("Model/market execution: not performed; run run_agent.py probe for a model-only check")
        return
    model = os.environ.get("AGENT_LLM_MODEL") or cfg.get("model", "")
    base = os.environ.get("AGENT_LLM_BASE") or cfg.get("base", "")
    key_present = any(os.environ.get(n) for n in
                      ("AGENT_LLM_KEY", cfg.get("key_env") or "AGENT_LLM_KEY", "OPENROUTER_API_KEY"))
    # Print only readiness, never credentials or their values.
    print("Model configured:", bool(base and model and not model.startswith("<")))
    print("API key available:", key_present)
    print("Model/market execution: not performed")
    print("State: isolated under round-4; no Round 3 imports or deployment commands")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "demo"))
    args = parser.parse_args()
    if args.command == "check":
        check()
    else:
        from offline_demo import demo
        demo()


if __name__ == "__main__":
    main()
