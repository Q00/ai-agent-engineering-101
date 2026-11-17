"""Local preparation helpers. No market registration, wallet loading or model calls."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"


def verify_vendor():
    source = json.loads((VENDOR / "SOURCE.json").read_text())
    for name, spec in source["files"].items():
        path = VENDOR / name
        if path.is_symlink():
            raise ValueError(f"Vendor file must not be a symlink: {name}")
        raw = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if blob != spec["git_blob_sha1"]:
            raise ValueError(f"Vendor source differs from recorded upstream blob: {name}")
    return len(source["files"])


def load_kit():
    verify_vendor()
    sys.path.insert(0, str(VENDOR))
    import agent_kit
    return agent_kit


def run_id(prefix):
    return prefix + "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


class NoWallet:
    """The fixture cannot grant signing/payment capabilities or expose identities."""
    url = None

    def grant(self, name, perm, seconds):
        if perm.get("wallet") or perm.get("identity"):
            raise PermissionError("Offline fixtures have no wallet or market identity")
        return None

    def revoke(self, cap):
        return []


def offline_context():
    return {"market": "http://offline.invalid", "name": "offline-fixture",
            "tokens": {}, "addresses": {}, "signer": NoWallet(), "shop_url": None}
