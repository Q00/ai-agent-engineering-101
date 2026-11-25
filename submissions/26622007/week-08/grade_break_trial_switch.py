"""Writes and removes price.grade_break_trial in pricing.json (profit_price.break_trial_arm reads it at every quote).

pricing.json is hand formatted and Terra edits it too, so the setting goes in as one line right after `"price": {` and
leaves as that line: every other byte stays as it was. The new text must parse to the old settings plus (or minus) the
trial and nothing else, and the file must still be what was read just before the swap, or nothing is written.
A copy of the old file goes to backup_dir first (O_EXCL, 0600); the swap is a temp file in the same folder, fsync and
os.replace with the old file's mode. Never over a trial that is already there: remove it first (switch_off).

  setting(salt=..., start_at=..., hours=9, ...) -> the dict, ValueError unless profit_price.trial_setting_ok passes
  switch_on(path, s, backup_dir=...) / switch_off(path, backup_dir=...) -> the backup's path
"""
import json
import os
import re
import time

import profit_price

KEY = "grade_break_trial"
_PRICE_OPEN = re.compile(r'"price"\s*:\s*\{')
_LINE = re.compile(r'\n([ \t]*)"grade_break_trial": (\{[^\n]*\}),?(?=\n)')


def setting(*, salt, start_at, hours=9, share=0.5, grade_break=None, categories=None, source=None):
    """A whole trial for [start_at, start_at + hours h): hours in (0, TRIAL_MAX_HOURS]. ValueError when it is not one
    break_trial_arm would run (a huge hours never reaches the sum: no OverflowError)."""
    if not (profit_price._trial_num(start_at) and profit_price._trial_num(hours)
            and 0 < hours <= profit_price.TRIAL_MAX_HOURS):
        raise ValueError("grade_break_trial: start_at and 0 < hours <= TRIAL_MAX_HOURS are required")
    s = {"share": share, "salt": salt, "grade_break": grade_break if grade_break is not None else {"B": 0.19},
         "start_at": start_at, "stop_at": start_at + hours * 3600,
         **({"categories": categories} if categories is not None else {}),
         **({"source": source} if source is not None else {})}
    if not profit_price.trial_setting_ok(s) or not (source is None or isinstance(source, str)):
        raise ValueError("grade_break_trial: bad setting")
    return s


def _read(path):
    with open(path, encoding="utf-8") as f:
        raw = f.read()
    d = json.loads(raw)
    if not isinstance(d, dict) or not isinstance(d.get("price"), dict):
        raise ValueError("grade_break_trial: pricing.json has no price object")
    return raw, d


def _swap(path, raw, new, backup_dir, stamp):
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    backup = os.path.join(backup_dir, f"pricing.json.bak-gbt-{stamp}")
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    mode = os.stat(path).st_mode & 0o7777
    tmp = f"{path}.tmp-gbt-{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        with open(path, encoding="utf-8") as f:      # another writer since the read: leave theirs
            if f.read() != raw:
                raise ValueError("grade_break_trial: pricing.json changed while writing; not replaced")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return backup


def switch_on(path, s, *, backup_dir, stamp=None):
    """Put setting s in as price.grade_break_trial. ValueError, the file left as it was, when s is not a whole setting,
    a trial is already there (ended or not), or the result would differ from the old settings plus s."""
    if not profit_price.trial_setting_ok(s):
        raise ValueError("grade_break_trial: bad setting")
    raw, d = _read(path)
    if KEY in d["price"]:
        raise ValueError("grade_break_trial: a trial is already in pricing.json; switch_off first")
    m = list(_PRICE_OPEN.finditer(raw))
    after = raw[m[0].end():] if len(m) == 1 else ""
    indent = re.match(r'\n([ \t]*)"', after)
    if not indent:
        raise ValueError("grade_break_trial: cannot place the line in pricing.json")
    line = "\n" + indent[1] + json.dumps(KEY) + ": " + json.dumps(s, ensure_ascii=False, allow_nan=False) + ","
    new = raw[:m[0].end()] + line + after
    if json.loads(new) != {**d, "price": {**d["price"], KEY: s}}:
        raise ValueError("grade_break_trial: the new pricing.json is not the old one plus the trial")
    return _swap(path, raw, new, backup_dir, stamp or time.strftime("%Y%m%d-%H%M%S"))


def switch_off(path, *, backup_dir, stamp=None):
    """Take price.grade_break_trial out (its one line). ValueError, the file left as it was, when there is none or it is
    not on one line of its own (then delete the key by hand)."""
    raw, d = _read(path)
    if KEY not in d["price"]:
        raise ValueError("grade_break_trial: no trial in pricing.json")
    m = list(_LINE.finditer(raw))
    if len(m) != 1:
        raise ValueError("grade_break_trial: the trial is not one line; delete price.grade_break_trial by hand")
    new = raw[:m[0].start()] + raw[m[0].end():]
    want = {**d, "price": {k: v for k, v in d["price"].items() if k != KEY}}
    try:
        ok = json.loads(new) == want
    except ValueError:
        ok = False
    if not ok:
        raise ValueError("grade_break_trial: removing the line does not give the old settings; delete it by hand")
    return _swap(path, raw, new, backup_dir, stamp or time.strftime("%Y%m%d-%H%M%S"))
