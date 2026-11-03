"""Run the stock course v4 through the student's strict-SKU lifecycle adapter."""
import os
import sys
from r3.state import ROOT
from r3.model import api_key
from r3.adapter import install, load_kit

if __name__ == "__main__":
    os.environ["PYTHONUTF8"] = "1"
    os.environ["MARKET_DIR"] = str(ROOT / ".market")
    key = api_key()
    if key:
        os.environ["OPENAI_API_KEY"] = key
    # Each child uses the verified interpreter rather than a stale global `python`.
    os.environ["PATH"] = str(__import__("pathlib").Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    install(load_kit()).main()
