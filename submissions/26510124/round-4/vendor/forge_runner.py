#!/usr/bin/env python3
"""Runs one forged tool in its own process: agent_kit.py starts it as  python -I forge_runner.py <tool dir> call|test
with a JSON request on stdin, and reads one JSON line from stdout.

The guard below enforces the tool's permission.yml on the usual paths (sockets, open(), subprocess): a host that is not
listed cannot be reached, a file that is not listed cannot be written, key files cannot be read. It keeps an honest tool
inside what it declared. It is not a security boundary against code written to escape it.

A tool's logic.py defines   def run(args, kit) -> anything JSON
and its test.py defines     def test(run, kit)   (raise or assert to fail)
"""
import builtins
import io
import json
import os
import socket
import sys
import traceback
import urllib.request
from pathlib import Path

MARK = "@@forge-result@@ "


def parse_permission(text):
    """The small YAML subset forge_tool writes: 'key: value', 'key: [a, b]', and one nested 'permission:' block."""
    out, cur = {}, None
    for raw in text.splitlines():
        line = raw.split(" #")[0].rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        nested = raw.startswith((" ", "\t"))
        key, _, val = line.strip().partition(":")
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            val = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
        elif val.lower() in ("true", "false"):
            val = val.lower() == "true"
        elif val.lstrip("-").isdigit():
            val = int(val)
        else:
            val = val.strip("'\"")
        if nested and cur is not None:
            out[cur][key] = val
        elif val == "":
            out[key], cur = {}, key
        else:
            out[key], cur = val, None
    return out


class Kit:
    """What a tool may ask of the kit: who it acts as, and the wallet (through the kit; keys never come here)."""

    def __init__(self, req, perm, testing):
        self.market, self.name, self.shop_url = req.get("market"), req.get("name"), req.get("shop_url")
        self.addresses = req.get("addresses") or {}
        self.entities = req.get("entities") or []
        self.tokens = req.get("tokens") or {}
        self._signer, self._cap, self._testing = req.get("signer"), req.get("cap"), testing
        self.testing = testing

    def token(self, role):
        if role not in self.tokens:
            if role not in self.entities:
                raise PermissionError(f"this agent runs without its {role} entity (the kit was started with only "
                                      f"{', '.join(self.entities) or 'none'}), so there is no {role} token")
            raise PermissionError(f"permission.identity of this tool does not include {role!r}")
        return self.tokens[role]

    def address(self, role):
        return self.addresses[role]

    @staticmethod
    def canon(obj):
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()

    def _wallet(self, op, role, **kw):
        if not self._signer:
            raise PermissionError("permission.wallet is empty: this tool may not use the wallet")
        body = json.dumps(dict(kw, op=op, role=role, cap=self._cap, testing=self._testing)).encode()
        req = urllib.request.Request(self._signer, data=body, headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=150) as r:
            out = json.load(r)
        if "error" in out:
            raise PermissionError(out["error"])
        return out["result"]

    def sign(self, obj, role):
        """base64 EIP-191 signature over canon(obj) by the role's wallet ('shop' or 'buyer')."""
        return self._wallet("sign", role, obj=obj)

    def sign_jwt(self, payload, role):
        """A JWT with alg EIP191 over canon(payload), signed by the role's wallet."""
        return self._wallet("sign_jwt", role, payload=payload)

    def pay(self, to, wei, data_hex="", role="buyer"):
        """Send wei tOKRW from the role's wallet with data (hex) and wait for the receipt; returns the tx hash.
        In a test it sends nothing and returns 0x followed by 64 zeros."""
        return self._wallet("pay", role, to=to, wei=int(wei), data=data_hex)


def guard(perm, root, signer, deny_ports=(), deny_hosts=()):
    """deny_ports: the mini app's port, on this computer; deny_hosts: its other addresses (this computer's LAN
    addresses, its public tunnel host), so a tool cannot answer for the page by another way in."""
    deny_hosts = {str(h).lower() for h in deny_hosts}
    allowed = {h.lower() for h in perm.get("network") or []}
    if signer:
        allowed.add("127.0.0.1")
    resolved = set()
    real_getaddrinfo, real_connect = socket.getaddrinfo, socket.socket.connect

    def host_ok(host):
        host = (host or "").lower().rstrip(".")
        return any(host == h or host.endswith("." + h) for h in allowed)

    def getaddrinfo(host, *a, **kw):
        h = host.decode() if isinstance(host, bytes) else str(host)
        if h.lower().rstrip(".") in deny_hosts and not h.replace(".", "").isdigit() and ":" not in h:
            raise PermissionError(f"network: {h} is the app's page; tools cannot reach it")
        if not host_ok(h):
            raise PermissionError(f"network: {h} is not in permission.network {sorted(allowed - {'127.0.0.1'})}")
        out = real_getaddrinfo(host, *a, **kw)
        resolved.update(x[4][0] for x in out)
        return out

    def connect(self, address):
        if isinstance(address, tuple) and len(address) >= 2 and address[1] in deny_ports and \
                str(address[0]).lower() in {"127.0.0.1", "::1", "localhost", "0.0.0.0"} | deny_hosts:
            raise PermissionError(f"network: port {address[1]} on this computer is the app's page; tools cannot reach it")
        if isinstance(address, tuple) and address[0] not in resolved and not host_ok(address[0]):
            raise PermissionError(f"network: {address[0]} is not in permission.network")
        return real_connect(self, address)
    socket.getaddrinfo, socket.socket.connect = getaddrinfo, connect

    writable = [(root / w).resolve() for w in perm.get("write") or []]
    real_open = builtins.open

    def guarded_open(file, mode="r", *a, **kw):
        if isinstance(file, int):
            return real_open(file, mode, *a, **kw)
        p = Path(file)
        p = (p if p.is_absolute() else Path.cwd() / p).resolve()
        if p.name.endswith(".key") or ".kit" in p.parts:
            raise PermissionError(f"{p.name}: the kit's secrets are not readable by tools")
        if any(c in mode for c in "wax+"):
            if not any(p == w or w in p.parents for w in writable):
                raise PermissionError(f"write: {p.relative_to(root) if root in p.parents else p} is not in permission.write")
            p.parent.mkdir(parents=True, exist_ok=True)
        return real_open(file, mode, *a, **kw)
    builtins.open = io.open = guarded_open

    if not perm.get("exec"):
        import subprocess

        def no_exec(*a, **kw):
            raise PermissionError("starting programs needs permission.exec: true")
        subprocess.Popen = subprocess.run = subprocess.call = subprocess.check_output = no_exec
        os.system = os.popen = no_exec


def main():
    tool, mode = Path(sys.argv[1]).resolve(), sys.argv[2]
    req = json.loads(sys.stdin.read() or "{}")
    perm = parse_permission((tool / "permission.yml").read_text(encoding="utf-8"))
    p = perm.get("permission") or {}
    root = Path(req.get("root") or Path.cwd()).resolve()
    os.chdir(root)
    sys.path.insert(0, str(tool))
    guard(p, root, req.get("signer"), tuple(req.get("deny_ports") or ()), tuple(req.get("deny_hosts") or ()))
    kit = Kit(req, p, mode == "test")
    try:
        ns = {"__name__": "logic", "__file__": str(tool / "logic.py")}
        exec(compile((tool / "logic.py").read_text(encoding="utf-8"), "logic.py", "exec"), ns)
        if not callable(ns.get("run")):
            raise TypeError("logic.py must define run(args, kit)")
        if mode == "test":
            tns = {"__name__": "test", "__file__": str(tool / "test.py")}
            exec(compile((tool / "test.py").read_text(encoding="utf-8"), "test.py", "exec"), tns)
            if not callable(tns.get("test")):
                raise TypeError("test.py must define test(run, kit)")
            out = {"ok": True, "result": tns["test"](ns["run"], kit)}
        else:
            out = {"ok": True, "result": ns["run"](req.get("args") or {}, kit)}
    except BaseException as e:                       # the agent reads this and fixes its tool
        tb = traceback.format_exc().splitlines()
        out = {"ok": False, "error": f"{type(e).__name__}: {e}", "trace": "\n".join(tb[-8:])}
    try:
        line = json.dumps(out, ensure_ascii=False, default=str)
    except (TypeError, ValueError) as e:
        line = json.dumps({"ok": False, "error": f"the result is not JSON: {e}"})
    sys.__stdout__.write("\n" + MARK + line + "\n")
    sys.__stdout__.flush()


if __name__ == "__main__":
    main()
