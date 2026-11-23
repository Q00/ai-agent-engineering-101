"""v1.20 buyer_log: per-buyer did-not-buy logging (append-only JSONL). LOGGING ONLY.

Nothing here is read by any decision path; every function swallows its own errors and returns None, so a broken
log can never change or fail a deal. Schema: LOGGING_SPEC_v120.md (schema_version 1).
Path: $R3_BUYER_LOG, default logs/buyer_attempts.jsonl (relative to the seller cwd). R3_BUYER_LOG=0 turns it off.
"""
import json
import os
import threading
import time
from pathlib import Path

SCHEMA = 1
_LOCK = threading.Lock()
_VISITS = {}          # buyer_ref -> request count seen by this process (repeat visits)
DEFAULT_MAX_BYTES = 50 * 1024 * 1024


def _max_bytes():
    """Parse R3_BUYER_LOG_MAX_BYTES; any bad value (non-int, <= 0) falls back to 50MB. Never raises at import."""
    try:
        v = int(os.environ.get("R3_BUYER_LOG_MAX_BYTES", DEFAULT_MAX_BYTES))
        return v if v > 0 else DEFAULT_MAX_BYTES
    except Exception:  # noqa: BLE001
        return DEFAULT_MAX_BYTES


MAX_BYTES = _max_bytes()   # stop writing past this size
_CAPPED = set()            # paths that already got the one cap marker line in this process
MAX_VISIT_KEYS = 50_000   # memory cap: the visit table is cleared past this many buyer refs (visit_n restarts at 1)


# v121b (CoS 결정 18:02): offer event carries the first 80 chars of the buyer offer text, secrets / tokens masked.
# Masking runs on the first 400 chars BEFORE the 80-char cut (a token across the 80-char edge is masked, not half-kept).
import re as _re

OFFER_TEXT_MAX = 80
MASK = "[MASKED]"
_SECRET_RX = [
    _re.compile(r"-----BEGIN [A-Z ]*-----.*?(?:-----END [A-Z ]*-----|$)", _re.S),          # PEM blocks
    _re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}|\bbasic\s+[A-Za-z0-9+/=]{16,}"),      # auth headers
    _re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|api[_\s-]?key|apikey|access[_\s-]?key|private[_\s-]?key)"
                r"\b(\s*[:=]\s*|\s+is\s+|\s+)\S+"),                                           # key=value / key value
    _re.compile(r"(?i)\b(auth(?:orization)?|session|cookie|otp|pin)\b(\s*[:=]\s*|\s+is\s+)\S+"),  # v121c: needs : = is
    _re.compile(r"(비밀번호|비번|패스워드|암호|密码|口令)(\s*[:=]?\s*)\S+"),                      # v121c: ko / zh password
    _re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{8,}"),                                         # sk-... style keys
    _re.compile(r"\b(?:gh[pousr]|github_pat|xox[abprs]|glpat)[-_][A-Za-z0-9_-]{8,}"),         # github / slack / gitlab
    _re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                                                     # AWS key id
    _re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),             # JWT
]
_BLOB_RX = _re.compile(r"[A-Za-z0-9_+/=.-]{24,}")                 # long blobs with letters AND digits (linear scan)
_PRE_CUT = 400                                                     # mask a bounded prefix (offer text can be long)
_CTRL_RX = _re.compile(r"[\x00-\x1f\x7f]+")


def _is_blob(s):
    return bool(_re.search(r"[A-Za-z]", s) and _re.search(r"[0-9]", s))


def mask_text(text, n=OFFER_TEXT_MAX):
    # -> (masked first n chars or None, masked_count). Never raises. Logging only.
    try:
        if not isinstance(text, str):
            return None, 0
        s, hits = text[:_PRE_CUT], [0]
        s, k = _pii(s)                                       # v121c: email / phone / card / RRN (pii_mask.py)
        hits[0] += k

        def sub(rx):
            def f(m):
                hits[0] += 1
                if rx.groups >= 2 and m.group(2) is not None:
                    return m.group(1) + m.group(2) + MASK
                return MASK
            return f
        for rx in _SECRET_RX:
            s = rx.sub(sub(rx), s)

        def blob(m):
            if _is_blob(m.group(0)) and m.group(0) != MASK:
                hits[0] += 1
                return MASK
            return m.group(0)
        s = _BLOB_RX.sub(blob, s)
        s = _CTRL_RX.sub(" ", s)
        return s[:n], hits[0]
    except Exception:  # noqa: BLE001
        return None, 0


def _load_pii():
    try:
        from pii_mask import mask_pii as f                 # v121c: top-level, stdlib only (not llm/)
        return f
    except Exception:  # noqa: BLE001 - hooks/ on sys.path only: load ../pii_mask.py by file path
        try:
            import importlib.util as _u
            spec = _u.spec_from_file_location("_r3_pii", str(Path(__file__).resolve().parents[1] / "pii_mask.py"))
            m = _u.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m.mask_pii
        except Exception:  # noqa: BLE001
            return None


_PII = _load_pii()


def _pii(s):
    if _PII is None:                                       # fail-closed: no masker -> no text kept
        return MASK, 1
    return _PII(s)


def clean(text, n):
    # v121c: masked prefix for the seller request / no_stock logs (was raw text[:n]). Never raises.
    try:
        t, _ = mask_text(text, n)
        return t
    except Exception:  # noqa: BLE001
        return None


def offer_fields(text):
    # -> {text, text_len, text_masked} for the seller offer event. Never raises.
    try:
        t, k = mask_text(text)
        return {"text": t, "text_len": len(text) if isinstance(text, str) else None, "text_masked": k}
    except Exception:  # noqa: BLE001
        return {}


def path():
    p = os.environ.get("R3_BUYER_LOG", "logs/buyer_attempts.jsonl")
    return None if p == "0" else Path(p)


def buyer_ref(context):
    """Best available buyer identity, never raises. Order: message metadata (buyer/buyer_id/agent/from/sender),
    call_context user name, A2A context_id. Returns (ref, source)."""
    try:
        msg = getattr(context, "message", None)
        md = getattr(msg, "metadata", None) or {}
        if hasattr(md, "items"):
            for k in ("buyer", "buyer_id", "agent", "from", "sender"):
                v = md.get(k)
                if isinstance(v, (str, int)) and str(v):
                    return str(v)[:120], "metadata." + k
        cc = getattr(context, "call_context", None)
        u = getattr(cc, "user", None)
        n = getattr(u, "user_name", None)
        if isinstance(n, str) and n and getattr(u, "is_authenticated", False):
            return n[:120], "call_context.user"
        cid = getattr(context, "context_id", None) or getattr(msg, "context_id", None)
        if cid:
            return "ctx:" + str(cid)[:120], "context_id"
    except Exception:  # noqa: BLE001
        pass
    return "unknown", "none"


def write(event, buyer, source, task, **fields):
    """Append one row. Never raises."""
    try:
        p = path()
        if p is None:
            return None
        row = {"v": SCHEMA, "ts": round(time.time(), 3), "event": event, "buyer": buyer, "buyer_src": source,
               "task": task}
        with _LOCK:
            try:
                if p.stat().st_size >= MAX_BYTES:               # size cap: stop, never rotate or truncate
                    if str(p) not in _CAPPED:                   # one marker line per process, then silence
                        _CAPPED.add(str(p))
                        with open(p, "a", encoding="utf-8") as f:
                            f.write(json.dumps({"v": SCHEMA, "ts": round(time.time(), 3), "event": "cap_reached",
                                                "max_bytes": MAX_BYTES}, sort_keys=True) + "\n")
                    return None
            except FileNotFoundError:
                pass
            if event == "request":
                if buyer not in _VISITS and len(_VISITS) >= MAX_VISIT_KEYS:
                    _VISITS.clear()
                _VISITS[buyer] = _VISITS.get(buyer, 0) + 1
                fields["visit_n"] = _VISITS[buyer]
                fields["repeat"] = _VISITS[buyer] > 1
            row.update(fields)
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n")
        return row
    except Exception:  # noqa: BLE001
        return None
