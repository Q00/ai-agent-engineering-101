"""Expose only the verified course-kit shop proxy using the official Pinggy SDK."""
import argparse
import time

import httpx
import pinggy


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    response = httpx.get(f"http://127.0.0.1:{args.port}/.well-known/agent-card.json", timeout=5)
    response.raise_for_status()
    if response.json().get("name") != "Seochanit Shop":
        raise RuntimeError("Refusing to expose an unexpected local service")
    tunnel = pinggy.start_tunnel(forwardto=f"127.0.0.1:{args.port}", type="http",
                                 httpsonly=True, autoreconnect=False)
    try:
        deadline = time.monotonic() + 70
        while time.monotonic() < deadline:
            urls = tunnel.urls
            https = next((url for url in urls if url.startswith("https://")), None)
            if https:
                print("TUNNEL_URL " + https, flush=True)
                break
            if tunnel.state.name in {"Stopped", "Ended", "Invalid"}:
                raise RuntimeError("Pinggy tunnel ended before provisioning")
            time.sleep(0.5)
        else:
            raise RuntimeError("Pinggy did not provision an HTTPS URL within 70 seconds")
        while tunnel.state.name not in {"Stopped", "Ended", "Invalid"}:
            time.sleep(1)
    finally:
        tunnel.stop()


if __name__ == "__main__":
    main()
