"""Global real-call ledger v4 (v1.17d, CR review_r3_v117_fixes3.md a784913b N1/N2/N3; CoS 18:40 proposer share).

Same JSONL file and event schema as v1.17c (realrun_ledger.py), read-compatible both ways for the events the old
code knows. Changes:

N1  the cap no longer resets when the ledger is deleted, emptied, cut back or moved.
    * The ledger path is PINNED (absolute constant LEDGER below). A Ledger object for any other path is refused
      unless allow_unpinned=True (tests only); copying the driver elsewhere no longer creates a fresh ledger.
    * A HIGH-WATER MARK file lives in a SEPARATE directory (HWM, ledger_hwm/realrun_ledger.hwm.json). After every
      append it records rows, bytes, sha256 of the whole ledger, committed and the cumulative reserved total.
    * Before every operation (under the flock): ledger missing -> refused; HWM missing -> refused; ledger shorter
      than the HWM (rows or bytes) or its first HWM.bytes bytes no longer hash to HWM.sha256 -> refused
      ("shrunk / rewritten, treated as full"). Nothing ever creates a ledger implicitly.
    * A new ledger only via init(approval_id) when BOTH files are absent; an existing ledger without a HWM only via
      adopt(approval_id) (one time, records the adopt event). Deleting BOTH files by hand is the one reset that the
      box cannot see: every event prints the ledger sha so it can be written into the approvals file (CR N1).
    * The cap check uses max(committed from rows, HWM committed) and never lets reserved_total_ever shrink.
N2  rounds carry a scope ("remeasure" | "proposer") and their own approval id. Several rounds may be open at once
    (the 18:40 CoS relay: re-measure 10 + proposer <= 10 share the same 20). SCOPE_CAP["proposer"] = 10.
N3  capture(round, raw_sha256): the raw_stdout sha is written to the ledger right after the Mac output is saved;
    ingest refuses a raw_stdout whose sha differs. A round is captured once.
Single host, flock (llm.filelock.locked); not a cross-host lock."""
import hashlib
import json
import os
import time
import uuid
from pathlib import Path

from llm.filelock import atomic_write_json, locked

LEDGER = Path("/workspace/course/r3_v117_llm/realrun_ledger.jsonl")            # pinned (N1)
HWM = Path("/workspace/course/r3_v117_llm/ledger_hwm/realrun_ledger.hwm.json")   # separate directory (N1)
CAP = 20
SCOPE_CAP = {"remeasure": CAP, "proposer": 10}
APPROVAL_REMEASURE = "approvals_r3_20261103.md 18:05 (성수님, CoS 중계): 재측정 허용, 새로 20회 이하, 주입 포함"
APPROVAL_PROPOSER = ("CoS 중계 18:40 (성수님): 제안자 실호출 회차 준비 <=10, 재측정 10과 같은 원장 상한 20 공유 "
                     "[EL은 원문 미확인, approvals 행 추가 필요]")


class LedgerError(RuntimeError):
    pass


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class Ledger:
    def __init__(self, path=None, hwm=None, cap=CAP, allow_unpinned=False):
        self.path = Path(path) if path is not None else LEDGER
        self.hwm = Path(hwm) if hwm is not None else (HWM if path is None else self.path.parent / "hwm" / "hwm.json")
        if not allow_unpinned and (self.path != LEDGER or self.hwm != HWM):
            raise LedgerError(f"ledger path is pinned to {LEDGER} / {HWM} (got {self.path} / {self.hwm})")
        if not self.path.is_absolute() or not self.hwm.is_absolute():
            raise LedgerError("ledger and hwm paths must be absolute")
        if self.hwm.parent == self.path.parent:
            raise LedgerError("the high-water mark must live in a separate directory")
        self.cap = int(cap)

    # ---- internal, call under lock
    def _raw(self):
        if not self.path.exists():
            return None
        return self.path.read_bytes()

    def _hwm(self):
        if not self.hwm.exists():
            return None
        try:
            h = json.loads(self.hwm.read_text(encoding="utf-8"))
            return {k: h[k] for k in ("rows", "bytes", "sha256", "committed", "reserved_total")}
        except Exception as e:                                            # noqa: BLE001
            raise LedgerError(f"hwm unreadable ({type(e).__name__}); treated as full") from e

    @staticmethod
    def _parse(raw):
        try:
            return [json.loads(x) for x in raw.decode("utf-8").splitlines() if x.strip()]
        except ValueError as e:
            raise LedgerError(f"ledger unreadable ({e}); treated as full") from e

    def _load(self):
        """-> rows. Every N1 check is here; any doubt = refused (treated as full)."""
        raw, h = self._raw(), self._hwm()
        if raw is None:
            raise LedgerError(f"ledger {self.path} missing (deleted or moved): refused, treated as full")
        if h is None:
            raise LedgerError("no high-water mark: refused (adopt(approval_id) once, explicitly)")
        if len(raw) < h["bytes"]:
            raise LedgerError(f"ledger shrunk ({len(raw)} < {h['bytes']} bytes): refused, treated as full")
        if _sha(raw[:h["bytes"]]) != h["sha256"]:
            raise LedgerError("ledger prefix rewritten (sha differs from the high-water mark): refused")
        rows = self._parse(raw)
        if len(rows) < h["rows"]:
            raise LedgerError("ledger has fewer rows than the high-water mark: refused")
        self._check_init(rows)
        if self._committed(rows) < h["committed"] - self._released_after(rows, h["rows"]) or \
                self._reserved_total(rows) < h["reserved_total"]:
            raise LedgerError("ledger totals are below the high-water mark: refused")
        return rows

    def _check_init(self, rows):
        if not rows or rows[0].get("event") != "init" or int(rows[0].get("cap", -1)) != self.cap:
            raise LedgerError("ledger has no matching init row")

    @staticmethod
    def _released_after(rows, k):
        return sum(int(r.get("released", 0)) for r in rows[k:] if r.get("event") == "close")

    def _write_hwm(self):
        raw = self._raw()
        rows = self._parse(raw)
        self.hwm.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.hwm, {"rows": len(rows), "bytes": len(raw), "sha256": _sha(raw),
                                     "committed": self._committed(rows), "reserved_total": self._reserved_total(rows),
                                     "ledger": str(self.path), "ts_kst": time.strftime("%Y-%m-%d %H:%M:%S")})
        return _sha(raw)

    def _append(self, row):
        row = dict(row, ts=round(time.time(), 3), ts_kst=time.strftime("%Y-%m-%d %H:%M:%S"))
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        row["ledger_sha256_after"] = self._write_hwm()
        return row

    @staticmethod
    def _state(rows):
        st = {}
        for r in rows:
            e, rd = r.get("event"), r.get("round")
            if e == "reserve":
                st[rd] = {"n": int(r["n"]), "nonce": r["nonce"], "sent": False, "closed": False, "used": None,
                          "scope": r.get("scope", "remeasure"), "approval_id": r.get("approval_id"),
                          "captured": None}
            elif e == "sent" and rd in st:
                st[rd]["sent"] = True
            elif e == "captured" and rd in st:
                st[rd]["captured"] = r["raw_sha256"]
            elif e == "close" and rd in st:
                st[rd].update(closed=True, used=int(r["used"]), released=int(r["released"]))
            elif e == "void":
                st.setdefault(rd, {"n": 0, "nonce": None, "sent": False, "closed": True, "used": 0, "released": 0,
                                   "scope": "remeasure", "captured": None})
        return st

    @staticmethod
    def _committed(rows):
        res = sum(int(r["n"]) for r in rows if r.get("event") == "reserve")
        rel = sum(int(r["released"]) for r in rows if r.get("event") == "close")
        return res - rel

    @staticmethod
    def _reserved_total(rows):
        return sum(int(r["n"]) for r in rows if r.get("event") == "reserve")

    def _scope_committed(self, rows, scope):
        st = self._state(rows)
        return sum(v["n"] - (v.get("released") or 0) for v in st.values() if v.get("scope") == scope and v["n"])

    # ---- creation (explicit only)
    def init(self, approval_id, scope_note):
        with locked(self.path):
            if self.path.exists() or self.hwm.exists():
                raise LedgerError("init refused: a ledger or high-water mark already exists")
            if not approval_id:
                raise LedgerError("init needs an explicit approval id")
            return self._append({"event": "init", "approval_id": approval_id, "cap": self.cap, "scope": scope_note})

    def adopt(self, approval_id):
        """One time: put a high-water mark on an existing v1.17c ledger (no HWM yet)."""
        with locked(self.path):
            raw = self._raw()
            if raw is None:
                raise LedgerError("adopt refused: ledger missing")
            if self.hwm.exists():
                raise LedgerError("adopt refused: high-water mark already exists")
            if not approval_id:
                raise LedgerError("adopt needs an explicit approval id")
            rows = self._parse(raw)
            self._check_init(rows)
            return self._append({"event": "adopt_hwm", "approval_id": approval_id,
                                 "prior_rows": len(rows), "prior_sha256": _sha(raw)})

    # ---- public
    def summary(self):
        with locked(self.path):
            rows = self._load()
            st = self._state(rows)
            c = self._committed(rows)
            return {"cap": self.cap, "committed": c, "remaining": self.cap - c,
                    "open": [k for k, v in st.items() if not v["closed"]], "rounds": st,
                    "reserved_total_ever": self._reserved_total(rows),
                    "ledger_sha256": _sha(self._raw()), "approval_id": rows[0]["approval_id"]}

    def reserve(self, round_name, n, scope="remeasure", approval_id=APPROVAL_REMEASURE):
        n = int(n)
        if n < 1:
            raise LedgerError("n must be >= 1")
        if scope not in SCOPE_CAP:
            raise LedgerError(f"unknown scope {scope!r}")
        if not approval_id:
            raise LedgerError("reserve needs an approval id")
        with locked(self.path):
            rows = self._load()
            st = self._state(rows)
            if round_name in st:
                raise LedgerError(f"{round_name} already in the ledger (never reused)")
            c = max(self._committed(rows), 0)
            if c + n > self.cap:
                raise LedgerError(f"cap {self.cap}: committed {c} + {n} > {self.cap}")
            sc = self._scope_committed(rows, scope)
            if sc + n > SCOPE_CAP[scope]:
                raise LedgerError(f"scope {scope} cap {SCOPE_CAP[scope]}: committed {sc} + {n}")
            nonce = uuid.uuid4().hex
            self._append({"event": "reserve", "round": round_name, "n": n, "nonce": nonce, "scope": scope,
                          "approval_id": approval_id})
            return nonce

    def _open(self, rows, round_name):
        s = self._state(rows).get(round_name)
        if not s or s["closed"]:
            raise LedgerError(f"{round_name} is not an open reserved round")
        return s

    def mark_sent(self, round_name):
        with locked(self.path):
            s = self._open(self._load(), round_name)
            if s["sent"]:
                raise LedgerError(f"{round_name} was already marked sent (single run)")
            return self._append({"event": "sent", "round": round_name, "nonce": s["nonce"]})

    def capture(self, round_name, raw_sha256):
        with locked(self.path):
            s = self._open(self._load(), round_name)
            if not s["sent"]:
                raise LedgerError(f"{round_name} was not marked sent")
            if s["captured"]:
                raise LedgerError(f"{round_name} output already captured ({s['captured'][:12]})")
            return self._append({"event": "captured", "round": round_name, "raw_sha256": raw_sha256})

    def close(self, round_name, proven_zero, basis):
        with locked(self.path):
            s = self._open(self._load(), round_name)
            if not s["sent"]:
                raise LedgerError(f"{round_name} was never sent: abandon it instead")
            used = 0 if proven_zero else s["n"]
            return self._append({"event": "close", "round": round_name, "used": used, "released": s["n"] - used,
                                 "basis": basis})

    def abandon(self, round_name, reason):
        with locked(self.path):
            s = self._open(self._load(), round_name)
            if s["sent"]:
                raise LedgerError(f"{round_name} was marked sent: ingest or close-uncertain (counts stay used)")
            return self._append({"event": "close", "round": round_name, "used": 0, "released": s["n"],
                                 "basis": f"abandoned before send: {reason}"})

    def round(self, round_name):
        with locked(self.path):
            return self._state(self._load()).get(round_name)
