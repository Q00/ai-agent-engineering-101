"""The shop's Round 3 negotiation rule, without a model or a market.

    python strategy_test.py          (writes logs/strategy-test.log)

Rule (shop_core.at_budget): an offer that clears the reserve, sits below my
ask, and is exactly one of the market's budget amounts (budget_levels.json)
is taken at once as the buyer's whole budget. Any other offer goes to the
harness as before, told that it is not a budget amount. A chair, asked 191,
reserve 180; the harness is pinned to counter 188 and records what it saw.
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import catalog
import shop_core
from negotiation_memory import Journal

HERE = Path(__file__).resolve().parent
LOG = HERE / "logs" / "strategy-test.log"
LEVELS = json.loads((HERE / "budget_levels.json").read_text(encoding="utf-8"))["levels"]
NOTE = " The receipt is signed with this shop's key."
lines: list[str] = []
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    line = f"  {'PASS' if ok else 'FAIL'}  {name}" + (f": {detail}" if detail else "")
    print(line, flush=True)
    lines.append(line)


async def one_offer(offer: int, levels: bool) -> tuple[dict, list[str]]:
    prompts: list[str] = []

    async def harness(prompt: str) -> str:
        prompts.append(prompt)
        return "188"
    shop = shop_core.Shop(harness, Journal([]), key=Ed25519PrivateKey.generate(),
                          asks={"chair": 191}.get, levels=(lambda i: tuple(LEVELS.get(i, ()))) if levels else None,
                          deal_note=NOTE)
    _, deal = await shop.handle("t-1", "I want to buy a chair", None)
    reply, _ = await shop.handle("t-1", str(offer), deal)
    return reply, prompts


async def main() -> int:
    catalog.use([it for it in json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))
                 if it["id"] in ("lamp", "chair", "bookshelf")])
    lines.append(f"budget levels: {LEVELS}")
    r, p = await one_offer(185, levels=True)
    check("185 is a chair budget amount: taken at once, no harness call",
          r["state"] == "completed" and r["receipt"]["price"] == 185 and not p, r["text"])
    r, p = await one_offer(185, levels=False)
    check("without the levels the same 185 goes to the harness (week 7 behaviour)",
          r["state"] == "input-required" and r.get("ask") == 188 and len(p) == 1, r["text"])
    r, p = await one_offer(183, levels=True)
    check("183 is not a budget amount: the harness decides, told so",
          r["state"] == "input-required" and len(p) == 1 and "not one of them" in p[0], r["text"])
    r, p = await one_offer(160, levels=True)
    check("160 is a budget amount below the reserve: never taken", r["state"] != "completed", r["text"])
    r, p = await one_offer(205, levels=True)
    check("205 is above the ask: bought at the ask rule, as always", r["state"] == "completed", r["text"])
    check("the deal note is said after the deal", r["text"].endswith(NOTE))
    tree = ast.parse((HERE / "worker" / "entry.py").read_text(encoding="utf-8"))
    real = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                and getattr(n.targets[0], "id", "") == "DEAL_NOTE")
    check("the Worker's DEAL_NOTE has no digits (buyers read numbers as prices)",
          not any(ch.isdigit() for ch in real), real.strip())
    passed = sum(results)
    lines.append(f"\n{passed}/{len(results)} passed -> {'PASS' if passed == len(results) else 'FAIL'}")
    print(lines[-1])
    LOG.parent.mkdir(exist_ok=True)
    LOG.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
