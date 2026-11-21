#!/usr/bin/env python3
"""Round 4 entrypoint using the selected CLI model. No live execution by default."""
import argparse
from datetime import datetime
import json
import os
import sys
from zoneinfo import ZoneInfo

from local_support import ROOT, load_kit
from claude_model import ClaudeModel

OPEN_AT = datetime(2026, 11, 24, 19, tzinfo=ZoneInfo("Asia/Seoul"))


def config():
    return json.loads((ROOT / "agent.json").read_text())


def probe():
    model = ClaudeModel(config())
    raw = model.complete([
        {"role": "system", "content": "This is a connectivity test with no market and no tools. Return exactly one JSON action: {\"thought\":\"connection checked\",\"action\":\"sleep\",\"args\":{\"seconds\":30,\"why\":\"model probe only\"}}."},
        {"role": "user", "content": "Return the action. Do not execute it."}], timeout=60)
    action = json.loads(raw)
    if action["action"] != "sleep" or action["args"].get("seconds") != 30:
        raise ValueError("Model did not follow the connectivity probe contract")
    print("PASS: selected Claude Opus CLI returned a verified decision; no market/tool/wallet execution")


def market_args(args, now=None):
    now = now or datetime.now(ZoneInfo("Asia/Seoul"))
    if now < OPEN_AT:
        raise ValueError("Round 4 opens 2026-11-24 19:00 KST; use preview_app.py before opening")
    return ["agent_kit.py", "run", "--dir", str(ROOT), "--market", "https://market.ourolabs.ai",
            "--name", args.name, "--tunnel", "--app", "8800"] + (["--app-tunnel"] if args.app_tunnel else [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="one model call, no market registration or wallets")
    run = sub.add_parser("run", help="registers on the live market after opening; not a test")
    run.add_argument("--name", default="dyishappy")
    run.add_argument("--app-tunnel", action="store_true")
    args = parser.parse_args()
    if args.command == "probe":
        probe()
        return
    argv = market_args(args)
    if not os.environ.get("MARKET_OWNER"):
        raise ValueError("MARKET_OWNER must be set in the environment; never put the claim code in a file")
    kit = load_kit()
    kit.Model = ClaudeModel
    sys.argv = argv
    kit.main()


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        raise SystemExit(str(error))
