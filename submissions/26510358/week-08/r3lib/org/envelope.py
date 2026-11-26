"""Signed calls between our departments: the same trust shape as the market (signed request in, signed receipt out)
and as MeshKit (find, call, proof, check), kept inside one machine.

  envelope = {from, to, skill, args, ts, nonce, sig}      sig: Ed25519 of the sender over the canonical JSON
  receipt  = {by, req, res, ts, sig}                      req/res: sha256 of the canonical envelope and result

A receiver rejects (fail-closed) an envelope whose sender is not in the registry, whose signature does not verify,
that is addressed to another department, older than MAX_SKEW_S, or whose nonce it has already seen.
Keys live in .market/org/keys (gitignored with .market/); the registry of public keys in .market/org/registry.json.
"""
import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .. import config

IDENTITIES = ("hook", "ceo", "purchasing", "logistics", "quality", "marketing")
MAX_SKEW_S = 60


class Rejected(Exception):
    pass


def org_dir():
    return config.MARKET_DIR / "org"


def canon(d):
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(d):
    return hashlib.sha256(canon(d)).hexdigest()


def _key(name):
    if name not in IDENTITIES:
        raise Rejected(f"unknown identity {name}")
    path = org_dir() / "keys" / f"{name}.pem"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        pem = Ed25519PrivateKey.generate().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                         serialization.NoEncryption())
        try:                                                   # O_EXCL: two processes never write two keys
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(pem)
        except FileExistsError:
            pass
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def registry():
    """name -> base64 public key, for every identity (created on first use)."""
    path = org_dir() / "registry.json"
    try:
        reg = json.loads(path.read_text())
    except (OSError, ValueError):
        reg = {}
    if set(reg) != set(IDENTITIES):
        for name in IDENTITIES:
            raw = _key(name).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            reg[name] = base64.b64encode(raw).decode()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(reg, indent=1))
        tmp.replace(path)
    return reg


def _verify(name, body, sig):
    pub = registry().get(name)
    if not pub:
        raise Rejected(f"{name} is not in the registry")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(base64.b64decode(sig), canon(body))
    except Exception:
        raise Rejected(f"bad signature from {name}")


def seal(sender, to, skill, args, now=None):
    body = {"from": sender, "to": to, "skill": skill, "args": args or {}, "ts": round(now or time.time(), 3),
            "nonce": secrets.token_hex(8)}
    return dict(body, sig=base64.b64encode(_key(sender).sign(canon(body))).decode())


class Gate:
    """Receiver-side checks for one department. Remembers nonces for MAX_SKEW_S (replays fail)."""

    def __init__(self, me):
        self.me = me
        self.seen = {}

    def open(self, env, now=None):
        now = now or time.time()
        if not isinstance(env, dict) or not {"from", "to", "skill", "args", "ts", "nonce", "sig"} <= set(env):
            raise Rejected("malformed envelope")
        body = {k: env[k] for k in ("from", "to", "skill", "args", "ts", "nonce")}
        _verify(env["from"], body, env["sig"])
        if env["to"] != self.me:
            raise Rejected(f"addressed to {env['to']}, not {self.me}")
        if abs(now - float(env["ts"])) > MAX_SKEW_S:
            raise Rejected("stale or future timestamp")
        self.seen = {n: t for n, t in self.seen.items() if now - t <= MAX_SKEW_S}
        if env["nonce"] in self.seen:
            raise Rejected("replayed nonce")
        self.seen[env["nonce"]] = now
        return body


def receipt(by, env, result, now=None):
    body = {"by": by, "req": digest(env), "res": digest(result), "ts": round(now or time.time(), 3)}
    return dict(body, sig=base64.b64encode(_key(by).sign(canon(body))).decode())


def check_receipt(rec, env, result):
    """The caller's check: the receipt is signed by the department it called, for this request and this result."""
    if not isinstance(rec, dict) or rec.get("by") != env["to"]:
        raise Rejected("receipt from the wrong department")
    body = {k: rec.get(k) for k in ("by", "req", "res", "ts")}
    _verify(rec["by"], body, rec.get("sig", ""))
    if rec["req"] != digest(env) or rec["res"] != digest(result):
        raise Rejected("receipt does not match the request or the result")
    return True
