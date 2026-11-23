"""Analyst proposals the person rejects in advance (person 11/15 ~14:20: logistics.aging_after_h 12 until the end of
11/17). analyst_auto_reject.json {"keys": {key: {"until": ts, "why": text}}, "summary_h": 9}:
  rule(key)      the active rule for a key (until not passed), else None
  note(...)      one row in logs/auto_reject.jsonl per proposal rejected this way (nothing goes to Telegram then)
  summary_due()  one Telegram a day (first analyst tick after summary_h): the rows of the last 24 h, per key
Read on every call; read-only for the shop and the buyer.
"""
import json
import os
import time
from collections import Counter
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "analyst_auto_reject.json")
LOG = os.path.join(HERE, "logs", "auto_reject.jsonl")
STATE = os.path.join(HERE, "logs", "auto_reject_state.json")


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def rule(key, now=None, path=None):
    r = (_read(path or RULES, {}).get("keys") or {}).get(key)
    return r if r and (now or time.time()) < float(r.get("until") or 0) else None


def note(key, value, reason, now=None, path=None):
    with open(path or LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": round(now or time.time()), "key": key, "value": value, "reason": reason[:200]},
                           ensure_ascii=False) + "\n")


def _rows(t0, t1, path=None):
    out = []
    try:
        with open(path or LOG, encoding="utf-8") as f:
            for ln in f:
                try:
                    x = json.loads(ln)
                except ValueError:
                    continue
                if t0 <= x.get("ts", 0) < t1:
                    out.append(x)
    except OSError:
        pass
    return out


def summary_text(now, path=None, rules_path=None):
    rows = _rows(now - 86400, now, path)
    keys = _read(rules_path or RULES, {}).get("keys") or {}
    c = Counter((x["key"], json.dumps(x["value"], ensure_ascii=False)) for x in rows)
    head = "분석관 자동 거절 요약 (최근 24시간)"
    if not rows:
        lines = ["- 없음"]
    else:
        lines = [f"- {k} -> {v}: {n}건" for (k, v), n in c.most_common()]
    lines += [f"  ({k}: {datetime.fromtimestamp(float(r['until'])):%m-%d %H:%M}까지 자동 거절)" for k, r in keys.items()
              if now < float(r.get("until") or 0)]
    return "\n".join([head] + lines)


def summary_due(now=None, send=None, path=None, rules_path=None, state_path=None, log=print):
    """Send summary_text once a day after summary_h while a rule is active (or rows came in the last 24 h).
    True when sent."""
    now = now or time.time()
    r = _read(rules_path or RULES, {})
    day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
    st = _read(state_path or STATE, {})
    if st.get("day") == day or datetime.fromtimestamp(now).hour < int(r.get("summary_h", 9)):
        return False
    active = any(now < float(x.get("until") or 0) for x in (r.get("keys") or {}).values())
    if not active and not _rows(now - 86400, now, path):
        return False
    if send is None:
        import alerts
        send = alerts.send
    send("daily", f"auto_reject_summary:{day}", summary_text(now, path, rules_path))
    tmp = (state_path or STATE) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"day": day, "ts": round(now)}, f)
    os.replace(tmp, state_path or STATE)
    log(f"auto reject summary sent ({day})")
    return True
