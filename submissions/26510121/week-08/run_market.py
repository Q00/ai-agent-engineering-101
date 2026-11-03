"""Run the stock course v4 through the student's strict-SKU lifecycle adapter."""
import os
import sys
from r3.state import ROOT
from r3.model import api_key
from r3.adapter import install, load_kit
from r3.transport import open_tunnel
from r3.lifecycle import market_session

if __name__ == "__main__":
    if "--dir" in sys.argv:
        from pathlib import Path
        requested_dir = Path(sys.argv[sys.argv.index("--dir") + 1]).resolve()
        if requested_dir != ROOT:
            raise SystemExit("This identity-bound adapter runs only the week-08 directory.")
    os.environ["PYTHONUTF8"] = "1"
    os.environ["MARKET_DIR"] = str(ROOT / ".market")
    key = api_key()
    if key:
        os.environ["OPENAI_API_KEY"] = key
    # Each child uses the verified interpreter rather than a stale global `python`.
    os.environ["PATH"] = str(ROOT / ".market" / "bin") + os.pathsep + str(__import__("pathlib").Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    kit = install(load_kit())
    kit.open_tunnel = lambda port: open_tunnel(kit, port)
    with market_session():
        kit.main()
