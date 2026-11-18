"""48 h warranty on B grade high-price goods (person's decision 11/09 ~11:30, after the market break rates: B 8.4%
overall, breakeven about margin / 3 at a 1.5x close-rate lift). Sofas keep their own rule (depts/sofa.py).
  on_cats     B grade high-price units get the warranty (TV, 냉방/환기/제습: B break 4.9% / 3.0%)
  trial_cats  between trial_since and trial_until, half of the B grade high-price units (by unit id, so a restart
              keeps the arm) get it, the other half not; tag b_warranty_trial (logs/b_warranty_trial.jsonl)
  everything else: no warranty on B grade (as before)
Normal shop case only, like the A grade warranty. Rules in b_warranty.json (read on every call).
"""
import hashlib
import json
import os
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES = os.path.join(HERE, "b_warranty.json")
LOG = os.path.join(HERE, "logs", "b_warranty_trial.jsonl")
TAG = "b_warranty_trial"


def rules():
    try:
        with open(RULES, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def leaf(u):
    return (u.get("category") or "").split(">")[-1]


def eligible(u, case="normal"):
    from depts import sofa
    from depts.qc import HIGH_MIN
    return (case == "normal" and u.get("grade") == "B" and u.get("kind") != "food"
            and (u.get("cost") or 0) > HIGH_MIN and not sofa.is_sofa(u.get("sku")))


def half(unit_id, share):
    return int(hashlib.md5(str(unit_id).encode()).hexdigest(), 16) % 1000 < share * 1000


def arm(u, case="normal", now=None, r=None):
    """'on' (on_cats), 'trial_on' / 'trial_off' (trial_cats inside the window), or None (rule not for this unit)."""
    r = rules() if r is None else r
    if not r.get("enabled") or not eligible(u, case):
        return None
    lf = leaf(u)
    if lf in (r.get("on_cats") or []):
        return "on"
    now = now or time.time()
    if lf in (r.get("trial_cats") or []) and (r.get("trial_since") or 0) <= now < (r.get("trial_until") or 0):
        return "trial_on" if half(u.get("unit"), float(r.get("trial_share", 0.5))) else "trial_off"
    return None


def hours(u, case, warranty_h, now=None):
    """Warranty hours for a B grade high-price unit under this rule, or None when the rule does not decide."""
    a = arm(u, case, now)
    if a is None:
        return None
    return warranty_h if a in ("on", "trial_on") else 0


def log_deal(task, u, a, warranty):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 1), "tag": TAG if a.startswith("trial") else "b_warranty_on",
                                "task": task, "unit": u.get("unit"), "sku": u.get("sku"), "cat": leaf(u), "arm": a,
                                "warranty": warranty}, ensure_ascii=False) + "\n")
    except OSError:
        pass
