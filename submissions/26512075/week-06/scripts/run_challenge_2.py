"""Run a directory of up to 20 independently configured A2A shops."""
from __future__ import annotations
import argparse

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CATEGORIES = ("lamp", "chair", "bookshelf")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main():
    parser = argparse.ArgumentParser()
    
    parser.add_argument("--shops", type=int, default=20)
    parser.add_argument("--budget", type=int, default=450)
    parser.add_argument("--output", type=Path, default=ROOT / "logs" / "challenge-2")
    args = parser.parse_args()
    if args.shops < 3:
        raise ValueError("Need at least three shops")
    args.output.mkdir(parents=True, exist_ok=True)
    processes, files, directory = [], [], []
    try:
        for index in range(args.shops):
            category = CATEGORIES[index % len(CATEGORIES)]
            port = free_port()
            config_path = args.output / f"shop-{index:02d}.json"
            config_path.write_text(json.dumps({
                "name": f"{category.title()} Shop {index:02d}",
                "description": f"Sells and negotiates {category} products.",
                "categories": [category],
                "reserve": {category: {"lamp": 100, "chair": 180, "bookshelf": 260}[category]
                            + 3 * (index // len(CATEGORIES))},
            }), encoding="utf-8")
            log = (args.output / f"shop-{index:02d}.log").open("w")
            files.append(log)
            proc = subprocess.Popen(
                [sys.executable, "seller.py", "--port", str(port), "--config", str(config_path)],
                cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=subprocess.STDOUT,
            )
            processes.append(proc)
            directory.append({"id": f"shop-{index:02d}", "url": f"http://127.0.0.1:{port}"})
        with httpx.Client(trust_env=False, timeout=3) as client:
            for shop, proc in zip(directory, processes):
                for _ in range(80):
                    try:
                        if client.get(shop["url"] + "/.well-known/agent-card.json").status_code == 200:
                            break
                    except httpx.RequestError:
                        if proc.poll() is not None:
                            raise RuntimeError(f"{shop['id']} exited early")
                        time.sleep(.15)
                else:
                    raise RuntimeError(f"{shop['id']} never served a card")
        cards = args.output / "directory.json"
        cards.write_text(json.dumps(directory, indent=2), encoding="utf-8")
        shopping = args.output / "shopping.json"
        shopping.write_text(json.dumps([{"item": "lamp"}, {"item": "chair"}], indent=2), encoding="utf-8")
        cmd = ["node", "dist/shopping-list.js", "--directory", str(cards), "--list",
               str(shopping), "--budget", str(args.budget)]
        run = subprocess.run(cmd, cwd=ROOT, env=os.environ.copy(), capture_output=True,
                             text=True, timeout=300, check=False)
        (args.output / "buyer.log").write_text(run.stderr + "\n" + run.stdout, encoding="utf-8")
        print(run.stdout or run.stderr[-1000:])
        if run.returncode:
            raise RuntimeError(f"Buyer exited {run.returncode}; inspect buyer.log")
        result = json.loads(run.stdout)
        assert len(result["receipts"]) == 2 and result["spent"] <= args.budget
        assert len({row["item"] for row in result["receipts"]}) == 2
    finally:
        for proc in processes:
            proc.terminate()
        for proc in processes:
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
        for stream in files:
            stream.close()

if __name__ == "__main__":
    main()



