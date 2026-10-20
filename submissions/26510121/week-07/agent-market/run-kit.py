"""Run the unmodified course kit with visible, bounded tunnel diagnostics."""
from datetime import datetime
import importlib.util
from pathlib import Path
import queue
import re
import subprocess
import threading
import time

import httpx


AGENT = Path(__file__).resolve().parent.parent
REPO = AGENT.parents[2]
spec = importlib.util.spec_from_file_location("course_market_kit", REPO / "scripts" / "market_kit.py")
kit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kit)


def open_tunnel(port):
    # This is exactly the kit's dedicated proxy, never an arbitrary local service.
    card = httpx.get(f"http://127.0.0.1:{port}/.well-known/agent-card.json", timeout=5)
    card.raise_for_status()
    if card.json().get("name") != "Seochanit Shop":
        raise RuntimeError("Refusing to expose an unexpected local service")
    path = AGENT / "logs" / "market-sessions" / datetime.now().strftime("tunnel-raw-%Y%m%d-%H%M%S-%f.log")
    proc = subprocess.Popen(["cloudflared", "tunnel", "--no-autoupdate", "--edge-ip-version", "4",
                             "--protocol", "http2", "--url", f"http://127.0.0.1:{port}"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace")
    lines = queue.Queue()

    def capture():
        with path.open("x", encoding="utf-8") as out:
            for line in proc.stdout:
                out.write(line)
                out.flush()
                lines.put(line)
        lines.put(None)

    threading.Thread(target=capture, daemon=True).start()
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=1)
        except queue.Empty:
            continue
        if line is None:
            proc.wait()
            raise RuntimeError(f"cloudflared exited {proc.returncode}; see {path.relative_to(AGENT)}")
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if match:
            return proc, match.group()
    kit.stop(proc)
    raise RuntimeError(f"cloudflared did not provision a tunnel; see {path.relative_to(AGENT)}")


if __name__ == "__main__":
    kit.open_tunnel = open_tunnel
    kit.main()
