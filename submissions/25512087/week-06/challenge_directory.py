"""Challenge 2 helpers for a 20-card directory and bounded shop selection."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import signal
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from buyer import BuyerRunner
from directory import AgentSummary, BudgetLedger, DirectoryIndex, NegotiationSummary
from harness import CommandHarness, Decision
from policy import BuyerPolicy
from protocol import A2AClient


class DirectoryHarness(Protocol):
    def decide(self, prompt: str, fallback: Decision) -> Decision:
        """Choose one shop from a small candidate set."""


@dataclass(frozen=True)
class DemoShop:
    card: AgentSummary
    reserves: dict[str, int]


def build_demo_shops(count: int = 20, first_port: int = 12000) -> list[DemoShop]:
    """Describe the local seller farm used in the many-shops demonstration."""
    shops = []
    for number in range(count):
        shops.append(
            DemoShop(
                card=AgentSummary(
                    name=f"Harness Shop {number:02d}",
                    url=f"http://127.0.0.1:{first_port + number}",
                    description="Negotiates lamp, chair, and bookshelf purchases over A2A.",
                    skills=("negotiate", "lamp", "chair", "bookshelf"),
                ),
                reserves={"lamp": 100, "chair": 180, "bookshelf": 260},
            )
        )
    return shops


def select_shop(
    item: str,
    candidates: list[AgentSummary],
    harness: DirectoryHarness,
) -> AgentSummary:
    """Let the harness select, but never escape the directory's candidate set."""
    if not candidates:
        raise ValueError(f"no shops advertise {item}")
    compact = [
        {
            "name": card.name,
            "url": card.url,
            "description": card.description,
            "skills": card.skills,
        }
        for card in candidates
    ]
    prompt = (
        f"Choose one shop for item={item!r}. Return exactly JSON with action=select, "
        "price=null, and the exact shop name in reason. Candidates: "
        + json.dumps(compact, ensure_ascii=False)
    )
    fallback = Decision("select", None, candidates[0].name)
    decision = harness.decide(prompt, fallback)
    return next((card for card in candidates if card.name in decision.reason), candidates[0])


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_card(url: str) -> AgentSummary:
    response = httpx.get(url + "/.well-known/agent-card.json", timeout=20)
    response.raise_for_status()
    value = response.json()
    skills = tuple(
        str(token)
        for skill in value.get("skills", [])
        for token in [
            skill.get("id"),
            skill.get("name"),
            skill.get("description"),
            *skill.get("tags", []),
            *skill.get("examples", []),
        ]
        if token
    )
    return AgentSummary(
        name=str(value["name"]),
        url=url,
        description=str(value.get("description", "")),
        skills=skills,
    )


def _wait_for_cards(urls: list[str], seconds: int = 45) -> list[AgentSummary]:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
                return list(pool.map(_read_card, urls))
        except (httpx.HTTPError, KeyError):
            time.sleep(0.5)
    raise TimeoutError("not all 20 sellers published Agent Cards")


def run_directory_demo(shop_count: int = 20, total_budget: int = 600) -> dict[str, object]:
    """Start 20 sellers, discover cards, and buy a three-item shopping list."""
    ports: list[int] = []
    while len(ports) < shop_count:
        port = _free_port()
        if port not in ports:
            ports.append(port)
    processes: list[subprocess.Popen] = []
    urls = [f"http://127.0.0.1:{port}" for port in ports]
    try:
        for number, port in enumerate(ports):
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "seller.py",
                        "--port",
                        str(port),
                        "--name",
                        f"Harness Shop {number:02d}",
                    ],
                    start_new_session=True,
                )
            )
        cards = _wait_for_cards(urls)
        directory = DirectoryIndex(cards)
        ledger = BudgetLedger(total_budget)
        harness = CommandHarness()
        budgets = {"lamp": 120, "chair": 200, "bookshelf": 280}
        used: set[str] = set()
        summaries: dict[str, str] = {}
        for item in ("lamp", "chair", "bookshelf"):
            candidates = [card for card in directory.search(item, limit=8) if card.url not in used][:5]
            selected = select_shop(item, candidates, harness)
            used.add(selected.url)
            summary = NegotiationSummary(item, selected.name)
            summary.add("directory", f"selected from {len(candidates)} shortlisted Agent Cards")
            client = A2AClient(selected.url)
            try:
                cap = min(budgets[item], ledger.remaining)
                result = BuyerRunner(client, BuyerPolicy(harness)).run(item, cap)
            finally:
                client.close()
            summary.add("result", json.dumps(result, ensure_ascii=False))
            if result.get("outcome") != "deal" or not isinstance(result.get("price"), int):
                raise RuntimeError(f"shopping item failed: {item}: {result}")
            ledger.record(item, selected.name, int(result["price"]))
            summaries[item] = summary.render()
            print(
                json.dumps(
                    {
                        "event": "challenge_2_purchase",
                        "item": item,
                        "shop": selected.name,
                        "price": result["price"],
                        "remaining": ledger.remaining,
                        "summary": summaries[item],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        return {
            "directory_cards": len(cards),
            "shopping_items": 3,
            "purchases": ledger.purchases,
            "spent": ledger.spent,
            "budget": ledger.total_budget,
            "all_bought": len(ledger.purchases) == 3,
            "duplicate_items": len(ledger.purchases) != len(set(ledger.purchases)),
            "summaries": summaries,
        }
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shops", type=int, default=20)
    parser.add_argument("--budget", type=int, default=600)
    args = parser.parse_args()
    result = run_directory_demo(args.shops, args.budget)
    print(json.dumps({"event": "challenge_2_result", **result}, ensure_ascii=False), flush=True)
    if not result["all_bought"] or result["spent"] > result["budget"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
