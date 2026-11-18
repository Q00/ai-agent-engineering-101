"""Keep tests off the live config and log files (11/09 ~19:30): every helper here points a module's path at a new
temp file, so a test never reads open_expiry.json, policy.json, soft_neg.json ... nor writes logs/*.
    from _isolate import tmp_json, tmp_path, policy
"""
import json
import os
import tempfile

_DIR = tempfile.mkdtemp(prefix="w08-test-")
os.environ.setdefault("W08_END_RULES", os.path.join(_DIR, "end_rules.json"))   # 11/12: limits reads it on import (off)


def tmp_path(name):
    """A path in this run's temp dir (the file is not created)."""
    return os.path.join(_DIR, name)


def tmp_json(obj, name=None):
    """A temp file holding obj as JSON; returns its path."""
    fd, p = tempfile.mkstemp(suffix=".json", dir=_DIR, prefix=(name or "rules") + "-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    return p


def policy(data=None):
    """policy.py reads a temp policy.json ({} = the code's DEFAULTS) and writes its history to a temp file."""
    import policy as P
    P.PATH = tmp_json(data or {}, "policy")
    P.HISTORY = tmp_path("policy_history.jsonl")
    P._cache.update(mtime=None, data=None, clipped=set())
    try:                                          # 11/11: the high margin filter file and its band log
        from depts import purchasing as PU
        PU.HIGH_MARGIN, PU.BAND_LOG = tmp_json({}, "high_margin"), tmp_path("high_margin_band.jsonl")
    except Exception:
        pass
    try:                                          # 11/11 ~18:25: the supply window rules (sec_per_line, guard), kit logs
        from depts import supply_window as W
        W.RULES, W.KIT_LOGS = tmp_path("supply_window.json"), [tmp_path("market-kit.log")]
    except Exception:
        pass
    try:                                          # 11/12 ~14:40: below cost only by the stoploss (off unless a test sets it)
        from depts import sales as SL
        SL.LOSS_RULE = tmp_path("loss_rule.json")
    except Exception:
        pass
    try:                                          # 11/12 ~15:15: db lock safety switch (off unless a test sets it)
        import dbutil
        dbutil.RULES = tmp_path("db_safe.json")
    except Exception:
        pass
    try:                                          # 11/12 ~18:30: buyer moves by grade (off unless a test sets it)
        from depts import grade_rule as GR
        GR.RULES, GR.CACHE = tmp_path("grade_rule.json"), tmp_path("shop_grades.json")
    except Exception:
        pass
    try:                                          # 11/12 ~14:20: the wider-buying trial B / C and its line log
        from depts import wide_trial as WT
        WT.RULES, WT.LINES = tmp_path("wide_trial.json"), tmp_path("wide_trial.jsonl")
    except Exception:
        pass
    try:                                          # 11/11 ~20:50: normal lines a run, over-1.5M rules, A grade cache
        from depts import normal_min, top_high
        normal_min.RULES, top_high.RULES = tmp_path("normal_min.json"), tmp_path("top_high.json")
        top_high.CACHE = tmp_path("cat_break_a.json")
    except Exception:
        pass
    try:                                          # 11/11 ~21:00: shop avoid rules, state, events, missed orders
        from depts import shop_avoid as SA
        SA.RULES, SA.STATE, SA.EVENTS = tmp_path("shop_avoid.json"), tmp_path("shop_avoid_state.json"), tmp_path("ev.jsonl")
        SA.MISSED = tmp_path("shop_avoid_missed.jsonl")
    except Exception:
        pass
    return P


def market_db():
    """11/13 ~13:40 (person): a context manager - a new temp market.db with the live schema (market_db.connect's
    CREATE TABLE IF NOT EXISTS, run on the temp path), and every sqlite3.connect of a "market.db" path (plain or a
    file: URI, any module) sent there, so the test never reads the live market.db. Yields the temp path."""
    import contextlib
    import sqlite3
    from unittest import mock
    import market_db as M
    path = tmp_path(f"market-{os.getpid()}-{id(object())}.db")
    real = sqlite3.connect

    def connect(database, *a, **k):
        s = str(database)
        if "market.db" in s.replace("\\", "/").split("/")[-1].split("?")[0]:
            if s.startswith("file:"):
                return real("file:" + path + ("?" + s.split("?", 1)[1] if "?" in s else ""), *a, **k)
            return real(path, *a, **k)
        return real(database, *a, **k)

    @contextlib.contextmanager
    def ctx():
        with mock.patch.object(sqlite3, "connect", connect), mock.patch.object(M, "DB_PATH", path):
            M.connect().close()
            yield path
    return ctx()
