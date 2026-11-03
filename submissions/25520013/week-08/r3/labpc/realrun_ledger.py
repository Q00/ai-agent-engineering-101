"""Global real-call ledger across realrun directories (v1.17c, CR fixes2 review R2 + EL 18:21 scope).

ONE append-only JSONL file (default /workspace/course/r3_v117_llm/realrun_ledger.jsonl), every read-check-append
under llm.filelock.locked (thread lock + fcntl.flock, single host). The cap is the WHOLE 18:05 approval:
re-measurement rounds + the next proposer round together <= CAP = 20 real calls. A new `prepare` never resets it.

Events (one JSON object per line):
  init     {approval_id, cap, scope}                      written once, on first use
  void     {round, reserved: 0, used: 0, reason}          a round that never reserved anything (e.g. 181106)
  reserve  {round, n, nonce, approval_id}                 counted BEFORE a batch exists
  sent     {round, nonce}                                 single-run mark: set once, before the Mac sends
  close    {round, used, released, basis}                 after ingest (or abandon of an unsent round)

committed = sum(reserve.n) - sum(close.released). reserve() refuses when committed + n > CAP or when another round
is still open (reserved, not closed). used = 0 (released = n) ONLY when the round was never sent (abandon) or the
labpc output proves no call started (INTEGRITY_FAIL / PREFLIGHT_FAIL line, no CALL line); every other close counts
all n as used (a cut-off run may have made calls whose output never came back). Unreadable ledger = full."""
import json
import os
import time
import uuid
from pathlib import Path

from llm.filelock import locked

HERE = Path(__file__).resolve().parents[1]
LEDGER = HERE / "realrun_ledger.jsonl"
CAP = 20
APPROVAL_ID = "approvals_r3_20261103.md 18:05 (성수님, CoS 중계): 재측정 허용, 새로 20회 이하, 주입 포함"
SCOPE = "18:05 승인 전체 합계: 재측정 회차 + 다음 제안자 회차"


class LedgerError(RuntimeError):
    pass


class Ledger:
    def __init__(self, path=None, cap=CAP, approval_id=APPROVAL_ID):
        self.path = Path(path if path is not None else LEDGER)       # resolved per call (tests patch LEDGER)
        self.cap, self.approval_id = int(cap), approval_id

    # ---- internal (call under lock)
    def _rows(self):
        if not self.path.exists():
            return []
        try:
            return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]
        except ValueError as e:
            raise LedgerError(f"ledger unreadable ({e}); treated as full") from e

    def _append(self, row):
        row = dict(row, ts=round(time.time(), 3), ts_kst=time.strftime("%Y-%m-%d %H:%M:%S"))
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (json.dumps(row, ensure_ascii=False) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        return row

    def _init(self, rows):
        if not rows:
            rows.append(self._append({"event": "init", "approval_id": self.approval_id, "cap": self.cap,
                                      "scope": SCOPE}))
        init = rows[0]
        if init.get("event") != "init" or int(init.get("cap", -1)) != self.cap:
            raise LedgerError("ledger has no matching init row")
        return rows

    @staticmethod
    def _state(rows):
        st = {}
        for r in rows:
            e, rd = r.get("event"), r.get("round")
            if e == "reserve":
                st[rd] = {"n": int(r["n"]), "nonce": r["nonce"], "sent": False, "closed": False, "used": None}
            elif e == "sent" and rd in st:
                st[rd]["sent"] = True
            elif e == "close" and rd in st:
                st[rd].update(closed=True, used=int(r["used"]), released=int(r["released"]))
            elif e == "void":
                st.setdefault(rd, {"n": 0, "nonce": None, "sent": False, "closed": True, "used": 0, "released": 0})
        return st

    def _committed(self, rows):
        res = sum(int(r["n"]) for r in rows if r.get("event") == "reserve")
        rel = sum(int(r["released"]) for r in rows if r.get("event") == "close")
        return res - rel

    # ---- public
    def summary(self):
        with locked(self.path):
            rows = self._rows()
            st = self._state(rows)
            return {"cap": self.cap, "committed": self._committed(rows), "remaining": self.cap - self._committed(rows),
                    "open": [k for k, v in st.items() if not v["closed"]], "rounds": st,
                    "approval_id": rows[0]["approval_id"] if rows else self.approval_id}

    def void(self, round_name, reason):
        with locked(self.path):
            rows = self._init(self._rows())
            if round_name in self._state(rows):
                raise LedgerError(f"{round_name} already in the ledger")
            return self._append({"event": "void", "round": round_name, "reserved": 0, "used": 0, "reason": reason})

    def reserve(self, round_name, n):
        n = int(n)
        if n < 1:
            raise LedgerError("n must be >= 1")
        with locked(self.path):
            rows = self._init(self._rows())
            st = self._state(rows)
            if round_name in st:
                raise LedgerError(f"{round_name} already in the ledger (never reused)")
            op = [k for k, v in st.items() if not v["closed"]]
            if op:
                raise LedgerError(f"round still open: {op} (ingest or abandon it first)")
            c = self._committed(rows)
            if c + n > self.cap:
                raise LedgerError(f"cap {self.cap}: committed {c} + {n} > {self.cap}")
            nonce = uuid.uuid4().hex
            self._append({"event": "reserve", "round": round_name, "n": n, "nonce": nonce,
                          "approval_id": self.approval_id})
            return nonce

    def mark_sent(self, round_name):
        with locked(self.path):
            rows = self._init(self._rows())
            s = self._state(rows).get(round_name)
            if not s or s["closed"]:
                raise LedgerError(f"{round_name} is not an open reserved round")
            if s["sent"]:
                raise LedgerError(f"{round_name} was already marked sent (single run)")
            return self._append({"event": "sent", "round": round_name, "nonce": s["nonce"]})

    def close(self, round_name, proven_zero, basis):
        with locked(self.path):
            rows = self._init(self._rows())
            s = self._state(rows).get(round_name)
            if not s or s["closed"]:
                raise LedgerError(f"{round_name} is not an open reserved round")
            used = 0 if proven_zero else s["n"]
            return self._append({"event": "close", "round": round_name, "used": used, "released": s["n"] - used,
                                 "basis": basis})

    def abandon(self, round_name, reason):
        with locked(self.path):
            rows = self._init(self._rows())
            s = self._state(rows).get(round_name)
            if not s or s["closed"]:
                raise LedgerError(f"{round_name} is not an open reserved round")
            if s["sent"]:
                raise LedgerError(f"{round_name} was marked sent: ingest its output instead (counts stay used)")
            return self._append({"event": "close", "round": round_name, "used": 0, "released": s["n"],
                                 "basis": f"abandoned before send: {reason}"})

    def nonce(self, round_name):
        with locked(self.path):
            s = self._state(self._rows()).get(round_name)
        return s and s["nonce"]
