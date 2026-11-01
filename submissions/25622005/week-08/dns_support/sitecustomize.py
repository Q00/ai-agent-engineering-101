"""Enable tunnel DNS recovery only in children of market_runtime.py."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from tunnel_dns import install
    install()
finally:
    sys.path.pop(0)
