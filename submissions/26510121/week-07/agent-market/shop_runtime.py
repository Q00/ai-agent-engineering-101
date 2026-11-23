"""Keep the same local seller and signing key when a free tunnel expires."""
import json
from pathlib import Path
import time


def run_shop(args, kit):
    directory = Path(args.dir).resolve()
    config = json.loads((directory / "agent.json").read_text(encoding="utf-8"))
    (directory / "logs").mkdir(exist_ok=True)
    kit.LOG_FILE = directory / "logs" / "market-kit.log"
    kit.log(f"[kit] market_kit {kit.KIT_VERSION}, {directory}; monitored shop transport")
    kit.ignore_pem(directory)
    key = kit.load_key(directory / "shop_key.pem")
    market = kit.Market(args.market, args.owner)
    seller = proxy = tunnel = None
    try:
        sport = kit.free_port()
        seller = kit.start(kit.fill(config["seller"]["cmd"], port=sport), directory)
        kit.pump(seller, "[seller]")
        if not kit.wait_card(f"http://127.0.0.1:{sport}"):
            raise RuntimeError("seller card did not become ready")
        proxy = kit.Proxy(args.port or kit.free_port())
        proxy.target(f"http://127.0.0.1:{sport}")
        failures = 0
        while True:
            if seller.poll() is not None:
                raise RuntimeError("seller exited; refusing to advertise a dead shop")
            try:
                tunnel, url = kit.open_tunnel(proxy.port)
                proxy.public = url
                kit.log(f"[tunnel] {url}, waiting until it answers")
                if not kit.wait_card(url, 120):
                    raise RuntimeError("public card did not become ready")
                joined = market.join("shop", args.name, key, url)
                kit.log(f"[shop] your seller on port {sport}; others reach it through the kit at {url}")
                kit.log(f"[market] shop registered as {joined['name']}, round {joined['round']}")
                started = time.monotonic()
                while tunnel.poll() is None:
                    if seller.poll() is not None:
                        raise RuntimeError("seller exited during tunnel session")
                    time.sleep(5)
                if time.monotonic() - started >= 300:
                    failures = 0
                raise RuntimeError("tunnel session ended")
            except (RuntimeError, OSError) as error:
                failures += 1
                if failures >= 3 or seller.poll() is not None:
                    raise RuntimeError("shop transport stopped after repeated failure") from error
                delay = 30 * failures
                kit.log(f"[tunnel] {type(error).__name__}; retrying in {delay}s with the same shop key")
            finally:
                kit.stop(tunnel)
                tunnel = None
            time.sleep(delay)
    except KeyboardInterrupt:
        kit.log("[kit] leaving the market")
    finally:
        kit.stop(tunnel)
        kit.stop(seller)
        if proxy is not None:
            proxy.server.should_exit = True
        market.c.close()
