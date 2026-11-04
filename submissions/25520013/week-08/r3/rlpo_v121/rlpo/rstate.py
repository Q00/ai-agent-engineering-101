# M2r persistent RLPO state (C4, T5/T6): OFF latch, anchor, consecutive-rollback counter, verified-version stack,
# open watch, bad (rolled back) versions, start gates, LLM breaker, CoS approvals. Fail-closed: a missing,
# unreadable or hash-mismatched state file reads as OFF. Writes: tmp + os.replace in the same directory.
import hashlib
import json
import os
from pathlib import Path

from rlpo import config as C


def _h(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def fresh(anchor_raw, verified_version, t):
    # initial state: OFF until the gates and the CoS enable it (owner 15:59); anchor = the policy bytes at RLPO start
    a = anchor_raw.decode("utf-8") if isinstance(anchor_raw, (bytes, bytearray)) else anchor_raw
    return {"enabled": False, "off_reason": "initial: start gates not passed", "mode": C.MODE,
            "anchor": json.loads(a), "anchor_sha": hashlib.sha256(a.encode("utf-8")).hexdigest(),
            "verified": [verified_version], "bad": [], "open_watch": None, "consec_rb": 0,
            "gates": {g: False for g in C.START_GATES + C.L_START_GATES}, "breaker": {}, "approvals": [], "t_init": t,
            "events": []}


def cr_ref_ok(ref):
    # B3: ref = "<path>#<sha256 hex prefix, >= 8>" and the file at path must have that sha256
    path, sep, h = str(ref).rpartition("#")
    h = h.strip().lower()
    if not sep or not path.strip() or len(h) < 8 or any(c not in "0123456789abcdef" for c in h):
        return False
    try:
        d = hashlib.sha256(Path(path.strip()).read_bytes()).hexdigest()
    except OSError:
        return False
    return d.startswith(h)


class Store:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            body, h = d["body"], d["hash"]
        except (OSError, ValueError, KeyError, TypeError) as e:
            return {"enabled": False, "off_reason": f"state unreadable ({type(e).__name__}): fail-closed", "_bad": True}
        if _h(body) != h:
            return {"enabled": False, "off_reason": "state hash mismatch: fail-closed", "_bad": True}
        return body

    def save(self, body):
        if body.get("_bad"):
            raise RuntimeError("refusing to save a fail-closed placeholder state")
        raw = (json.dumps({"body": body, "hash": _h(body)}, sort_keys=True, ensure_ascii=False, indent=1) + "\n").encode()
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        tmp.write_bytes(raw)
        os.replace(tmp, self.path)
        return body

    def init(self, anchor_raw, verified_version, t):
        if self.path.exists():
            raise RuntimeError("state exists; init refuses to overwrite it")
        return self.save(fresh(anchor_raw, verified_version, t))

    def latch_off(self, reason, t):
        b = self.load()
        if b.get("_bad"):
            return b                      # already OFF (fail-closed); nothing to write
        b["enabled"] = False
        b["off_reason"] = str(reason)[:500]
        b.setdefault("events", []).append({"t": t, "ev": "off", "reason": str(reason)[:300]})
        return self.save(b)

    def set_gate(self, gate, ok, by, ref, t):
        b = self.load()
        if b.get("_bad") or gate not in C.START_GATES + C.L_START_GATES:
            raise RuntimeError(f"cannot set gate {gate!r}")
        if by not in C.GATE_SETTERS or not isinstance(ref, str) or not ref.strip():
            raise PermissionError(f"L4: gate needs by in {sorted(C.GATE_SETTERS)} and a ref")
        if gate.startswith("cr_") and (by != C.CR_GATE_SETTER or not cr_ref_ok(ref)):
            raise PermissionError(f"B3: CR gate {gate!r} needs by={C.CR_GATE_SETTER} and ref=<CR report path>#<sha256 prefix>"
                                  f" matching the file")
        b["gates"][gate] = bool(ok)
        b["events"].append({"t": t, "ev": "gate", "gate": gate, "ok": bool(ok), "by": by, "ref": ref})
        return self.save(b)

    def enable(self, by, channel, ref, t):
        # turning the proposal-only loop ON: approver = CoS (owner 15:59), all start gates must be passed
        b = self.load()
        if b.get("_bad"):
            raise RuntimeError("state is fail-closed; re-init by a human")
        if by != C.APPROVER or channel not in C.APPROVAL_CHANNELS or not ref:
            raise PermissionError(f"enable needs approver {C.APPROVER} via {sorted(C.APPROVAL_CHANNELS)} with a ref")
        miss = [g for g in C.START_GATES if not b["gates"].get(g)]
        if miss:
            raise PermissionError(f"start gates not passed: {miss}")
        b["enabled"] = True
        b["off_reason"] = None
        b["consec_rb"] = 0
        b["events"].append({"t": t, "ev": "enable", "by": by, "channel": channel, "ref": ref})
        return self.save(b)
