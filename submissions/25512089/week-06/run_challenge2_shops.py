from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx


SHOPS_PATH = Path("challenge2_shops.json")
LOG_DIR = Path("logs/challenge-2/shops")

A2A_HEADERS = {
    "A2A-Version": "1.0",
}


def load_shops() -> list[dict]:
    return json.loads(
        SHOPS_PATH.read_text(
            encoding="utf-8"
        )
    )


def wait_until_ready(
    shop: dict,
    timeout: float = 30.0,
) -> bool:

    card_url = (
        shop["url"].rstrip("/")
        + "/.well-known/agent-card.json"
    )

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                card_url,
                headers=A2A_HEADERS,
                timeout=2.0,
            )

            if response.status_code == 200:
                return True

        except Exception:
            pass

        time.sleep(0.25)

    return False


def main() -> None:

    shops = load_shops()

    if len(shops) != 20:
        raise RuntimeError(
            f"Expected 20 shops, got {len(shops)}"
        )

    LOG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    processes: list[
        tuple[
            dict,
            subprocess.Popen,
            object,
        ]
    ] = []

    env = os.environ.copy()

    print(
        "[CHALLENGE2 LAUNCHER] "
        f"starting {len(shops)} shops",
        flush=True,
    )

    try:
        # -------------------------------------
        # Start all 20 A2A sellers
        # -------------------------------------
        for shop in shops:

            shop_id = shop["shop_id"]
            port = int(shop["port"])

            log_path = (
                LOG_DIR
                / f"{shop_id}.log"
            )

            log_file = log_path.open(
                "w",
                encoding="utf-8",
            )

            command = [
                sys.executable,
                "challenge2_shop.py",
                "--shop-id",
                shop_id,
                "--port",
                str(port),
            ]

            process = subprocess.Popen(
                command,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,
                text=True,
            )

            processes.append(
                (
                    shop,
                    process,
                    log_file,
                )
            )

            print(
                "[CHALLENGE2 STARTED] "
                f"{shop_id} "
                f"pid={process.pid} "
                f"url={shop['url']}",
                flush=True,
            )

        # -------------------------------------
        # Verify all 20 Agent Cards
        # -------------------------------------
        ready = 0

        for shop, process, _ in processes:

            if process.poll() is not None:
                raise RuntimeError(
                    f"{shop['shop_id']} "
                    f"exited early with "
                    f"code {process.returncode}"
                )

            ok = wait_until_ready(
                shop
            )

            if not ok:
                raise RuntimeError(
                    f"{shop['shop_id']} "
                    f"did not become ready at "
                    f"{shop['url']}"
                )

            ready += 1

            print(
                "[CHALLENGE2 READY] "
                f"{shop['shop_id']} "
                f"card={shop['url']}"
                f"/.well-known/agent-card.json",
                flush=True,
            )

        print(
            "[CHALLENGE2 READY ALL] "
            f"{ready}/20 shops ready",
            flush=True,
        )

        print(
            "[CHALLENGE2 LAUNCHER] "
            "Press Ctrl+C after the "
            "Challenge 2 buyer finishes.",
            flush=True,
        )

        # Keep launcher alive.
        while True:

            for shop, process, _ in processes:

                code = process.poll()

                if code is not None:
                    raise RuntimeError(
                        f"{shop['shop_id']} "
                        f"stopped unexpectedly "
                        f"with exit code {code}"
                    )

            time.sleep(1.0)

    except KeyboardInterrupt:

        print(
            "\n[CHALLENGE2 LAUNCHER] "
            "stopping shops...",
            flush=True,
        )

    finally:

        for _, process, _ in processes:

            if process.poll() is None:
                process.terminate()

        deadline = time.monotonic() + 5.0

        for _, process, _ in processes:

            remaining = max(
                0.0,
                deadline - time.monotonic(),
            )

            try:
                process.wait(
                    timeout=remaining
                )
            except subprocess.TimeoutExpired:
                process.kill()

        for _, _, log_file in processes:
            log_file.close()

        print(
            "[CHALLENGE2 LAUNCHER] "
            "all shops stopped.",
            flush=True,
        )


if __name__ == "__main__":
    main()