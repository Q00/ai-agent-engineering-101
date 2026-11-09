"""Two development runs against the instructor's unchanged kit seller."""
import contextlib
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path

from buyer import buy


BASE = Path(__file__).resolve().parent


def check():
    spec = importlib.util.spec_from_file_location(
        "course_kit", BASE / "development_support" / "trade_test_week06.py")
    kit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kit)
    for item, budget, expected_deals in [("lamp", 120, 1), ("bookshelf", 230, 0)]:
        record = {"messages": 0, "continued": 0, "offers": [], "deals": [],
                  "canceled": 0, "posts": 0, "bad_version": 0}
        port = kit.free_port()
        server = kit.serve_kit_seller(port, record)
        url = f"http://127.0.0.1:{port}"
        try:
            assert kit.wait_card(url), "Kit seller failed to start"
            buy(url, item, budget)
            print(json.dumps({"item": item, "budget": budget, "record": record}), flush=True)
            assert len(record["deals"]) == expected_deals, record
            assert all(price <= budget for price in record["offers"]), record
            assert record["bad_version"] == 0 and record["continued"] > 0, record
        finally:
            server.should_exit = True
    print("PASS: buyer development checks; official full trade test remains pending", flush=True)


if __name__ == "__main__":
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
    log_dir = BASE / "development_logs"
    log_dir.mkdir(exist_ok=True)
    with (log_dir / f"buyer_connection_{stamp}.txt").open("x", encoding="utf-8") as capture:
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            try:
                check()
            except Exception as error:
                print(f"FAILED: {type(error).__name__}: {error}", flush=True)
                raise
