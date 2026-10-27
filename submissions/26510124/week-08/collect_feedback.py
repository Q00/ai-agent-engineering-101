"""Read-only sidecar: two bounded GETs every 15 minutes, zero LLM calls."""
import argparse
import fcntl
import json
from pathlib import Path
import signal
import threading
import time

import httpx

from feedback import (MAX_BYTES, append_event, atomic_json, evolve, load_json,
                      public_snapshot, recent_events, validate_config)

ROOT = Path(__file__).resolve().parent


def fetch_json(client, url):
    started = time.monotonic()
    with client.stream("GET", url) as response:
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_bytes(16384):
            content.extend(chunk)
            if len(content) > MAX_BYTES or time.monotonic() - started > 20:
                raise ValueError("Public response exceeds size/time budget")
    return json.loads(content)


def refresh(config, root=ROOT, client=None, now=None):
    """Failure leaves the last known snapshot untouched; its TTL still applies."""
    validate_config(config)
    now = time.time() if now is None else now
    cache_path = root / ".market" / "seller-strategy.json"
    previous = load_json(cache_path)
    if (isinstance(previous, dict) and 0 <= now - previous.get("updated_at", 0)
            < config["interval_seconds"]):
        return "cached"
    owned = client is None
    client = client or httpx.Client(timeout=10, follow_redirects=False)
    try:
        # Fixed official origin: URLs from competitor cards are never fetched.
        board = fetch_json(client, "https://market.ourolabs.ai/api/board")
        shops = fetch_json(client, "https://market.ourolabs.ai/api/shops")
        snapshot = public_snapshot(board, shops, config["name"], now)
        events = recent_events(root / "logs" / "seller-events.jsonl", now)
        state = evolve(config, snapshot, events, previous)
        append_event(root / "logs" / "strategy-decisions.jsonl", state)
        atomic_json(cache_path, state)
        return "updated"
    finally:
        if owned:
            client.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=ROOT / "feedback-config.json")
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    config = json.loads(args.config.read_text())
    validate_config(config)
    (ROOT / ".market").mkdir(exist_ok=True)
    with (ROOT / ".market" / "feedback.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("A feedback collector is already running")
        stopped = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stopped.set())
        while not stopped.is_set():
            try:
                print(f"[feedback] {refresh(config)}", flush=True)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                append_event(ROOT / "logs" / "feedback-errors.jsonl",
                             {"time": time.time(), "error": type(exc).__name__, "detail": str(exc)[:300]})
                print(f"[feedback] {type(exc).__name__}: retained old snapshot", flush=True)
                if args.once:
                    raise SystemExit(1)
            if args.once:
                break
            # Failure retries wait the full interval too; no aggressive retry loop.
            stopped.wait(config["interval_seconds"])


if __name__ == "__main__":
    main()
