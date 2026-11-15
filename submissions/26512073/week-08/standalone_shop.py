"""Register and serve the student's A2A and ACP shop directly."""
import argparse
import asyncio
import base64
import contextlib
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import socket
import sys
import time

import httpx
import uvicorn
from cryptography.hazmat.primitives import serialization

from checkout import write_checkout_state
from seller import build, canon


def log(message):
    print(f"{datetime.now():%H:%M:%S} [standalone] {message}", flush=True)


def check_port(port):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


class ShopMarket:
    def __init__(self, client, base, directory, name, key, owner, public_url):
        self.client = client
        self.base = base.rstrip("/")
        self.directory = Path(directory)
        self.name, self.key = name, key
        self.owner, self.public_url = owner, public_url
        self.token = None

    async def join(self):
        proof = {"name": self.name, "role": "shop", "url": self.public_url,
                 "ts": int(time.time())}
        public_key = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        body = {**proof, "pubkey": base64.b64encode(public_key).decode("ascii"),
                "sig": base64.b64encode(self.key.sign(canon(proof))).decode("ascii")}
        if self.owner:
            body["owner"] = self.owner
        response = await self.client.post(self.base + "/api/join", json=body)
        response.raise_for_status()
        result = response.json()
        self.token = result["token"]
        os.environ["MARKET_SHOP_TOKEN"] = self.token
        log(f"shop registered as {result['name']}, round {result['round']}")
        return result

    async def refresh_inventory(self):
        response = await self.client.get(
            self.base + "/api/inventory",
            headers={"Authorization": f"Bearer {self.token}"})
        if response.status_code == 401:
            await self.join()
            response = await self.client.get(
                self.base + "/api/inventory",
                headers={"Authorization": f"Bearer {self.token}"})
        response.raise_for_status()
        inventory = response.json()
        if not isinstance(inventory, dict) or not isinstance(inventory.get("units"), list):
            raise ValueError("Market returned an invalid inventory")
        write_checkout_state(self.directory / ".market" / "inventory.json", inventory)
        return inventory


async def read_tunnel(process, found):
    while line := await process.stdout.readline():
        text = line.decode("utf-8", errors="replace").rstrip()
        log("cloudflared " + text)
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", text)
        if match and not found.done():
            found.set_result(match.group())
    if not found.done():
        found.set_exception(RuntimeError("cloudflared stopped before publishing its URL"))


async def wait_public(client, url, tunnel):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if tunnel is not None and tunnel.returncode is not None:
            raise RuntimeError("The public tunnel stopped")
        try:
            card = await client.get(url + "/.well-known/agent-card.json", timeout=10)
            acp = await client.get(url + "/.well-known/acp.json", timeout=10)
            if card.status_code == acp.status_code == 200 \
                    and card.json()["supportedInterfaces"][0]["url"] == url + "/" \
                    and acp.json()["endpoint"] == url + "/acp":
                log("public A2A card and student ACP descriptor verified")
                return
        except (httpx.HTTPError, ValueError, KeyError, IndexError):
            pass
        await asyncio.sleep(2)
    raise RuntimeError("Public A2A and ACP endpoints did not answer within 120 seconds")


async def serve(args):
    directory = Path(args.dir).resolve()
    os.environ["MARKET_DIR"] = str(directory / ".market")
    os.environ["MARKET_URL"] = args.market.rstrip("/")
    key = serialization.load_pem_private_key(
        (directory / "shop_key.pem").read_bytes(), password=None)
    # Refuse an occupied port before starting a second shop.
    check_port(args.port)
    app = build(args.port)
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=args.port, use_colors=False,
        proxy_headers=True, forwarded_allow_ips="127.0.0.1"))
    # asyncio.run owns Ctrl+C so cleanup can stop both the server and tunnel.
    server.capture_signals = contextlib.nullcontext
    server_task = asyncio.create_task(server.serve())
    tunnel, tunnel_reader = None, None
    try:
        deadline = time.monotonic() + 15
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("Seller server stopped during startup")
            if time.monotonic() > deadline:
                raise RuntimeError("Seller server did not start")
            await asyncio.sleep(0.1)
        public_url = args.public_url.rstrip("/") if args.public_url else None
        if args.tunnel:
            executable = shutil.which("cloudflared")
            if executable is None:
                raise RuntimeError("cloudflared is missing from PATH")
            tunnel = await asyncio.create_subprocess_exec(
                executable, "tunnel", "--no-autoupdate", "--url",
                f"http://127.0.0.1:{args.port}", stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT)
            found = asyncio.get_running_loop().create_future()
            tunnel_reader = asyncio.create_task(read_tunnel(tunnel, found))
            public_url = await asyncio.wait_for(found, timeout=60)
        app.state.public_url = public_url
        app.state.agent_card.supported_interfaces[0].url = public_url + "/"
        log(f"student A2A and ACP server at {public_url}")
        async with httpx.AsyncClient(timeout=30) as client:
            await wait_public(client, public_url, tunnel)
            market = ShopMarket(client, args.market, directory, args.name, key,
                                os.environ.get("MARKET_OWNER"), public_url)
            await market.join()
            inventory = await market.refresh_inventory()
            app.state.refresh_inventory = market.refresh_inventory
            log(f"inventory ready: {len(inventory['units'])} units; selling existing stock")
            while not server_task.done():
                if tunnel is not None and tunnel.returncode is not None:
                    raise RuntimeError("Tunnel stopped; restart to publish a fresh address")
                await asyncio.sleep(10)
                try:
                    await market.refresh_inventory()
                except (httpx.HTTPError, ValueError, OSError) as error:
                    log(f"inventory refresh failed ({type(error).__name__}); retrying")
            await server_task
    finally:
        server.should_exit = True
        if tunnel is not None and tunnel.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                tunnel.terminate()
            try:
                await asyncio.wait_for(tunnel.wait(), timeout=5)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    tunnel.kill()
                await tunnel.wait()
        if tunnel_reader is not None:
            await asyncio.gather(tunnel_reader, return_exceptions=True)
        await server_task
        os.environ.pop("MARKET_SHOP_TOKEN", None)
        log("student shop stopped")


class ConsoleLog:
    def __init__(self, console, files):
        self.console, self.files = console, files

    def write(self, text):
        self.console.write(text)
        for stream in self.files:
            stream.write(text)
        self.flush()
        return len(text)

    def flush(self):
        self.console.flush()
        for stream in self.files:
            stream.flush()

    def __getattr__(self, name):
        return getattr(self.console, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["join"])
    parser.add_argument("--dir", default=str(Path(__file__).resolve().parent))
    parser.add_argument("--market", required=True)
    parser.add_argument("--name", default="Afford Shop")
    parser.add_argument("--port", type=int, default=8900)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tunnel", action="store_true")
    group.add_argument("--public-url")
    args = parser.parse_args()
    if args.name != "Afford Shop":
        parser.error("The registered name must match the seller receipt name Afford Shop")
    if args.public_url and not args.public_url.startswith("https://"):
        parser.error("The public URL must use HTTPS")
    directory = Path(args.dir).resolve()
    (directory / "logs").mkdir(parents=True, exist_ok=True)
    console_out, console_err = sys.stdout, sys.stderr
    for stream in (console_out, console_err):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    with (directory / "logs" / "standalone-shop.log").open("a", encoding="utf-8") as own_log, \
            (directory / "logs" / "market-kit.log").open("a", encoding="utf-8") as course_log:
        sys.stdout = ConsoleLog(console_out, [own_log, course_log])
        sys.stderr = ConsoleLog(console_err, [own_log, course_log])
        try:
            log("starting student shop without the course kit checkout")
            asyncio.run(serve(args))
        except KeyboardInterrupt:
            pass
        except Exception as error:
            log(f"startup or runtime failed ({type(error).__name__}): {error}")
            raise SystemExit(1) from error
        finally:
            sys.stdout, sys.stderr = console_out, console_err


if __name__ == "__main__":
    main()
