"""ASGI negotiation endpoint. Full market checkout uses the local kit proxy."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "seller"))
from seller_r3 import build_app

app = build_app(int(os.getenv("PORT", "9999")), os.getenv("PUBLIC_BASE_URL"))
