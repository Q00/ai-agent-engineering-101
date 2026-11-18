"""Exactly one batch-model call; no market registration or payment."""
import asyncio
from pathlib import Path
from market_tools import ROOT, build_context
from policy import prepare

if __name__ == "__main__":
    target = ROOT / "model-policy.json"
    if target.exists():
        raise SystemExit("A model policy already exists. Preserve it; use runtime-policy.json for later adaptation.")
    asyncio.run(prepare(build_context(), target))
