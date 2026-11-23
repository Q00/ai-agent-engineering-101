"""Shared helpers for the attempt-2 kernel (human code, not editable by the model).

Data root, round end, hashing of other players, secret masking, JSON and log-tail readers. Stdlib only so the
kernel runs on labpc with plain python3.
"""

import calendar
import hashlib
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
ROUND_END = calendar.timegm((2026, 11, 17, 14, 59, 0, 0, 0, 0))  # 2026-11-17 23:59 KST
GOAL = "최종 순위표 Everyone 보기의 total 1위 (Class 보기의 student_total은 참고)"
DEFAULT_ROOT = "~/ai-agent-engineering-101/submissions/25520013/week-09"

_PEM = re.compile(r"-----BEGIN [A-Z ]*-----.*?-----END [A-Z ]*-----", re.S)
_TOKEN = re.compile(r"\b(?:sk|ghp|gho|ghs|github_pat|xox[abp])[-_][A-Za-z0-9_-]{10,}\b")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{10,}")
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
)
_URL = re.compile(r"https?://[^\s\"'<>]+")
IDENTITY_KEYS = ("owner", "github", "player", "email", "url", "endpoint", "card")


def data_root(root=None):
    return os.path.expanduser(root or os.environ.get("R3A2_ROOT") or DEFAULT_ROOT)


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def owner_login(root):
    """Our GitHub login from policy.json. Used only to find our own rows; never put in model input."""
    pol = load_json(os.path.join(root, "policy.json"), {}) or {}
    return ((pol.get("owner") or {}).get("github") or "").strip().lower()


def hid(value):
    """Stable short id for another participant (sha1, 6 hex)."""
    return "h" + hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:6]


def is_mine(row, login):
    if not login or not isinstance(row, dict):
        return False
    probe = {k: row.get(k) for k in ("owner", "player", "github")}
    return login in json.dumps(probe, ensure_ascii=False).lower()


def settle_side(data, login):
    """Our side of a settle: "shop" (our shop sold), "buyer" (our buyer bought) or None.

    The market sets own_owner on both kinds (실측 11-05: 24시간 5,286건 중 판매 2,764, 구매 2,522), so the buyer field
    decides which side we were on.
    """
    if not isinstance(data, dict) or data.get("own_owner") is not True:
        return None
    if login and login in json.dumps(data.get("buyer"), ensure_ascii=False).lower():
        return "buyer"
    return "shop"


def mask(text, secrets=()):
    """Remove keys, tokens, emails, URLs and the given literal secrets from text."""
    if not isinstance(text, str):
        return text
    text = _PEM.sub("[KEY]", text)
    text = _TOKEN.sub("[TOKEN]", text)
    text = _BEARER.sub("Bearer [TOKEN]", text)
    text = _EMAIL.sub("[EMAIL]", text)
    text = _URL.sub("[URL]", text)
    for s in secrets:
        if s and len(s) >= 3:
            text = re.sub(re.escape(s), "[OWNER]", text, flags=re.I)
    return text


def scrub(obj, login=""):
    """Copy of obj with identity fields of other participants hashed and our own marked as 우리."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            lk = str(k).lower()
            if lk in IDENTITY_KEYS:
                if (
                    lk == "owner"
                    and isinstance(v, str)
                    and login
                    and v.lower() == login
                ):
                    out[k] = "우리"
                elif v not in (None, ""):
                    out[k] = hid(v)
            elif lk in ("author", "shop", "buyer", "seller") and isinstance(
                v, str
            ):
                out[k] = v if login and login in v.lower() else hid(v)
            else:
                out[k] = scrub(v, login)
        return out
    if isinstance(obj, list):
        return [scrub(v, login) for v in obj]
    if isinstance(obj, str):
        return mask(obj, (login,))
    return obj


def tail_json(path, max_bytes, needle=None):
    """Yield parsed JSON lines from the last max_bytes of a JSONL file. needle (bytes) pre-filters lines."""
    try:
        fh = open(path, "rb")
    except OSError:
        return
    with fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        if size > max_bytes:
            fh.readline()
        for line in fh:
            if needle is not None and needle not in line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def fmt(x):
    if not isinstance(x, (int, float)) or isinstance(x, bool):
        return "-"
    if abs(x) >= 1e5:
        return f"{x / 1e6:.1f}M"
    return f"{x:.3g}"


def kst(ts, pattern="%m-%d %H:%M"):
    return datetime.fromtimestamp(ts, KST).strftime(pattern)


def cap(text, max_chars):
    """Cut an observation to max_chars and say so, with how to narrow the query."""
    if len(text) <= max_chars:
        return text
    return (
        text[:max_chars]
        + f"\n[잘림: 전체 {len(text)}자 중 {max_chars}자만 보임. 기간, SKU, 개수 조건을 좁혀 다시 조회]"
    )


def now_ts():
    return time.time()
