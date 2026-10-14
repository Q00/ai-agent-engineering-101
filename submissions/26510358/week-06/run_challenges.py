"""Execute actual large-catalog and 20-live-shop challenges, preserving each run.

This runner never fabricates test outcomes: result summaries are written after
real calls to the configured harness and A2A sellers, including failed runs.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any
import uuid

import httpx

from buyer import negotiate
from catalog import load_catalog
from evidence import numeric_price
from policy import StrategyPolicy
from shopping import read_directory, shop_all
from tracing import Trace

ROOT = Path(__file__).resolve().parent


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves existing run artifacts, including failures.
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalShops:
    """Own local seller processes and console artifacts for one challenge run."""

    def __init__(self, directory: Path, policy_path: str | None = None):
        self.directory, self.policy_path = directory, policy_path
        self.processes: list[subprocess.Popen] = []
        self.streams = []
        self.urls: list[str] = []
        self.configs: list[dict[str, Any]] = []

    def start(self, configs: list[dict[str, Any]]) -> list[str]:
        used_ports: set[int] = set()
        for index, config in enumerate(configs, start=1):
            shop_directory = self.directory / f"shop-{index:02d}"
            shop_directory.mkdir(parents=True, exist_ok=True)
            port = _free_port()
            while port in used_ports:
                port = _free_port()
            used_ports.add(port)
            complete = {**config, "log_dir": str(shop_directory / "trace"),
                        "catalog": str(ROOT / "data/catalog.json")}
            if self.policy_path:
                complete["policy"] = str(Path(self.policy_path).resolve())
            target = shop_directory / "config.json"
            _write_json(target, complete)
            console = (shop_directory / "console.log").open("xb")
            self.streams.append(console)
            command = [sys.executable, "-u", str(ROOT / "seller.py"), "--port", str(port), "--config", str(target)]
            process = subprocess.Popen(command, cwd=ROOT, stdout=console, stderr=subprocess.STDOUT, start_new_session=True)
            self.processes.append(process)
            self.urls.append(f"http://127.0.0.1:{port}")
            self.configs.append({"url": self.urls[-1], "config": complete, "command": command, "pid": process.pid})
        deadline = time.monotonic() + 40
        pending = set(range(len(self.urls)))
        with httpx.Client(timeout=1, trust_env=False, headers={"A2A-Version": "1.0"}) as client:
            while pending:
                for index in list(pending):
                    if self.processes[index].poll() is not None:
                        raise RuntimeError(f"seller {index + 1} exited before its Agent Card was ready")
                    try:
                        response = client.get(self.urls[index] + "/.well-known/agent-card.json")
                        response.raise_for_status()
                        if response.json().get("supportedInterfaces"):
                            pending.remove(index)
                    except (httpx.HTTPError, ValueError):
                        pass
                if pending and time.monotonic() >= deadline:
                    raise TimeoutError(f"Agent Cards did not become ready: {sorted(pending)}")
                if pending:
                    time.sleep(0.15)
        _write_json(self.directory / "processes.json", self.configs)
        return list(self.urls)

    def close(self):
        for process in self.processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGINT)
                except ProcessLookupError:
                    pass
        for process in self.processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait()
        for stream in self.streams:
            stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def shop_configs() -> list[dict[str, Any]]:
    """Three everyday specialists and seventeen genuinely advertised distractors."""
    configs = []
    for category in ("lamp", "chair", "bookshelf"):
        configs.append({"name": f"Everyday {category.title()} Shop", "categories": [category],
                        "reserve_offset": 0,
                        "description": f"Everyday practical {category} specialist. A large catalog of standard, affordable home products; willing to negotiate."})
    for category in ("lamp", "chair", "bookshelf"):
        configs.append({"name": f"Premium {category.title()} Atelier", "categories": [category],
                        "reserve_offset": 150,
                        "description": f"Premium collectible {category} specialist with elevated prices. Designer products for customers seeking luxury rather than budget home essentials."})
    for index, category in enumerate(("desk", "table", "cabinet", "sofa", "bed", "stool", "bench") * 2, start=1):
        configs.append({"name": f"Specialist {category.title()} Store {index}", "categories": [category],
                        "reserve_offset": index * 2,
                        "description": f"Specialist shop selling only {category} products, with a broad catalog. Does not stock other categories."})
    return configs


def run_catalog_challenge(url: str, log_dir: Path, policy: StrategyPolicy, timeout: float) -> dict[str, Any]:
    trace = Trace("challenge-1", log_dir)
    description = "something wooden to keep about 40 books, not taller than 85 cm"
    budget = 300
    catalog = load_catalog()
    trace.write("challenge_started", query=description, budget=budget, catalog_size=len(catalog))
    started = time.monotonic()
    try:
        result = negotiate(url, description, budget, policy=policy,
                           trace=Trace("buyer", log_dir / "buyer"), timeout=timeout)
        receipt = result.get("receipt", {})
        selected_id = receipt.get("itemId", receipt.get("item_id", receipt.get("item")))
        product = next((item for item in catalog if item["id"] == selected_id), None)
        attributes = product["attributes"] if product else {}
        checks = {
            "completed": result.get("status") == "completed",
            "catalog_has_thousands": len(catalog) >= 2000,
            "selected_catalog_product": product is not None,
            "wooden": attributes.get("material") == "wood",
            "book_capacity": attributes.get("capacity_books", 0) >= 40,
            "height": 0 < attributes.get("height_cm", 0) <= 85,
            "within_budget": isinstance(receipt.get("price"), (int, float)) and 0 < receipt["price"] <= budget,
            "reserve_respected": product is not None and isinstance(receipt.get("price"), (int, float)) and receipt["price"] >= product["reserve"],
        }
        summary = {"passed": all(checks.values()), "checks": checks, "query": description,
                   "budget": budget, "selected_product": product, "result": result,
                   "catalog_size": len(catalog), "elapsed_seconds": round(time.monotonic() - started, 3)}
    except Exception as exc:
        summary = {"passed": False, "query": description, "budget": budget,
                   "error": f"{type(exc).__name__}: {exc}", "elapsed_seconds": round(time.monotonic() - started, 3)}
    trace.write("challenge_summary", summary=summary)
    _write_json(log_dir / "summary.json", summary)
    return summary


def run_shopping_challenge(directory: list[dict[str, Any]], log_dir: Path,
                           policy: StrategyPolicy, timeout: float) -> dict[str, Any]:
    entries = [{"key": "lamp", "item": "lamp"},
               {"key": "chair", "item": "chair"},
               {"key": "bookshelf", "item": "bookshelf"}]
    _write_json(log_dir / "directory.json", directory)
    _write_json(log_dir / "shopping-list.json", {"total_budget": 600, "items": entries})
    try:
        summary = shop_all(directory, entries, 600, log_dir=log_dir / "transactions", policy=policy, timeout=timeout)
    except Exception as exc:
        summary = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
    summary["directory_size"] = len(directory)
    summary["directory_has_20_live_shops"] = len(directory) == 20
    summary["passed"] = summary["passed"] and summary["directory_has_20_live_shops"]
    _write_json(log_dir / "summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--challenge", choices=("all", "1", "2"), default="all")
    parser.add_argument("--policy")
    parser.add_argument("--timeout", type=float, default=600,
                        help="Overall shopping deadline; individual negotiations remain limited to 240 seconds")
    args = parser.parse_args()
    numeric_price(args.timeout)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    policy = StrategyPolicy(args.policy)
    challenge_1_dir = ROOT / "logs/challenge-1" / run_id
    challenge_2_dir = ROOT / "logs/challenge-2" / run_id
    process_dir = challenge_2_dir / "shops" if args.challenge in {"all", "2"} else challenge_1_dir / "shops"
    results = {}
    with LocalShops(process_dir, args.policy) as shops:
        configs = shop_configs() if args.challenge in {"all", "2"} else [shop_configs()[2]]
        urls = shops.start(configs)
        directory = read_directory(urls, Trace("directory", process_dir))
        if args.challenge in {"all", "1"}:
            shelf_url = urls[2] if len(urls) == 20 else urls[0]
            results["challenge_1"] = run_catalog_challenge(shelf_url, challenge_1_dir, policy, min(240, args.timeout))
        if args.challenge in {"all", "2"}:
            results["challenge_2"] = run_shopping_challenge(directory, challenge_2_dir, policy, args.timeout)
    print(json.dumps({"run_id": run_id, "results": results}, ensure_ascii=False), flush=True)
    raise SystemExit(0 if all(result["passed"] for result in results.values()) else 1)


if __name__ == "__main__":
    main()
