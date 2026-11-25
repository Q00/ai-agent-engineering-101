"""Write a windowed buyer study switch: .market/buyer_final_m1.json, buyer_moss_first.json or buyer_our_ask.json.

ax-pro-cycle step 5 (main instruction 10, 11/10 12:55): a timed study has a finite start and end both where it is treated
(oneshot.final_m1_on, buyer_moss_first._switch, buyer_our_ask._switch) and where its switch is made, so no one writes a
window by hand that has no end, starts in the past, or runs past the study's maximum. This writer:
- takes a finite start and end inside the study's bounds (final_m1 0 < end - start <= 12 h; moss_first and our_ask 9-12 h),
  a salt for the A/B studies (none for the final_m1 shadow), and a start after now: the file is written before its
  window, so its mtime is before the start, as the readers require. now is the real clock, never an earlier one the
  caller gives (independent review 7b17f3f I);
- never writes over a window that is running (started, not over) or starts within SOON seconds: a running window is not
  moved, cut or extended; one not yet started can be replaced by another future one (review 523fd38 N1);
- keeps every window it writes and every off in HISTORY, append-only, so what ran is known whatever the switch file now
  says (review 77cdbef I1, M1: a record kept only in the switch was lost when the next window was written). A window
  ran if it started before it was stopped, replaced or over. Each write line keeps the window it replaced and each off
  line the window it stopped, so a window written by hand is not forgotten by off or by the next write (review 523fd38
  M1). Every time a window was on is its own run, never merged, so writing the same window again keeps the run that
  ran (review 4eaf0b6 I1); a write keeps the stopped record it overwrites and a second off keeps the first's (M1).
  With HISTORY gone the switch's stopped record stands in; when what ran is not known at all (a switch with neither,
  or an off that found it so) the next window needs new_study (N1). Only what HISTORY or the switch still says is
  known: a HISTORY deleted by hand loses the windows before the last. An off that cannot read HISTORY marks the past
  unknown (adversarial e0a5379 F1). Writers take turns on LOCK, a clock behind HISTORY refuses, and the running check
  is made again just before the rename (F2-F4). Not covered: a process killed between the rename and its HISTORY line
  (the next write or off records that window as replaced or stopped) and an off on a switch that is a directory or
  unreadable (exit 2, reported); a clock stepped back to between a write line and its start (review 4948771 M2); a
  history line with a future at refuses writes of that study until then (M3, fail closed: mend the line by hand). An
  off that could not take the lock exits 3 and says to run it again; one that took it late looks again (M1). Then a later window of the same study:
  - an A/B study (moss_first, our_ask) never reuses the salt of a window that ran: the same arms again would be the same
    study made longer; with a new salt it may start before the old end only if new_study (--new-study) says so;
  - final_m1 starts only after the end of every window that ran, and then only with new_study (review 77cdbef M2: off
    and on again gave a 24 h shadow);
- writes and syncs a temporary file in the same directory, then writes its HISTORY line, checks again that no window
  started and the switch did not change, and only then renames it over the switch, so no reader sees a window HISTORY
  does not have and a slow disk cannot cut a window that started meanwhile (GPT Pro mwa9krfw I-1, I-2); then it reads
  it back with the study's own reader at the start; if that reader would not take it, the old file is put back, an
  abort line ends the window in HISTORY and it is refused; HISTORY that cannot take the line refuses with nothing
  written; if putting it back fails too, the switch is removed (off for every reader), so no window stays
  without its HISTORY line, and the command exits 2 (review 523fd38 M2). The file keeps the switch's mode (0644 when new) so the buyer reads it as before (review 7b17f3f M3);
- off writes {"enabled": false} (with the window it stopped), always allowed: stopping a study is never refused, and
  its HISTORY line is written if it can be.

  python3 buyer_switch_write.py [--market-dir .market] final_m1 --start EPOCH --end EPOCH
  python3 buyer_switch_write.py [--market-dir .market] moss_first --start EPOCH --end EPOCH --salt SALT
  python3 buyer_switch_write.py [--market-dir .market] our_ask --off
  python3 buyer_switch_write.py [--market-dir .market] moss_first --start EPOCH --end EPOCH --salt NEW --new-study

It prints the JSON written and its mtime; exit 2 when refused.
"""
import argparse
import errno
import fcntl
import json
import math
import os
import re
import sys
import tempfile
import time

import buyer_moss_first
import buyer_our_ask
import oneshot

HISTORY = os.path.join("buyer_switch_write", "history.jsonl")
LOCK = os.path.join("buyer_switch_write", ".lock")   # writers take turns (adversarial e0a5379 F2)
SOON = 10   # s: a window starting this soon counts as running, so the guard and the rename cannot straddle its start
STUDIES = {
    "final_m1": {"file": oneshot.FINAL_M1_SWITCH, "min_hours": 0, "max_hours": oneshot.FINAL_M1_MAX_HOURS, "salt": False,
                 "reads": lambda md, start: oneshot.final_m1_on(md, env={}, now=start)},
    # the A/B readers check the window and mtime <= start themselves (no now): assign checks now
    "moss_first": {"file": buyer_moss_first.SWITCH, "min_hours": buyer_moss_first.MIN_HOURS,
                   "max_hours": buyer_moss_first.MAX_HOURS, "salt": True,
                   "reads": lambda md, _start: buyer_moss_first._switch(md) is not None},
    "our_ask": {"file": buyer_our_ask.SWITCH, "min_hours": buyer_our_ask.MIN_HOURS, "max_hours": buyer_our_ask.MAX_HOURS,
                "salt": True, "reads": lambda md, _start: buyer_our_ask._switch(md) is not None},
}


class Refused(ValueError):
    pass


def _epoch(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and abs(v) < 1e12 and math.isfinite(v)


def _study(name):
    if name not in STUDIES:
        raise Refused(f"unknown study {name!r}")
    return STUDIES[name]


def _stopped(path):
    """The window an off wrote into the switch ({"start", "end", "salt", "stop"}), or None."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError, RecursionError):
        return None
    s = d.get("stopped") if isinstance(d, dict) else None
    if isinstance(s, dict) and _epoch(s.get("start")) and _epoch(s.get("end")) and _epoch(s.get("at")):
        return {"start": s["start"], "end": s["end"], "salt": _salt(s.get("salt")), "stop": s["at"]}
    return None


def _current(path):
    """(the switch's bytes or None, its enabled window as a dict or None)."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, None
    return raw, _current_window(raw)


def _current_window(raw):
    """The enabled window in switch bytes as {"start", "end", "salt"?}, or None."""
    if raw is None:
        return None
    try:
        d = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    if isinstance(d, dict) and d.get("enabled") is True and _epoch(d.get("start")) and _epoch(d.get("end")):
        return {"start": d["start"], "end": d["end"], "salt": _salt(d.get("salt"))}   # review 4eaf0b6 N2
    return None


def _history(md, name):
    """This study's HISTORY events in order; Refused if the file is there but cannot be read (fail closed)."""
    p = os.path.join(md, HISTORY)
    try:
        with open(p, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    except OSError as e:
        raise Refused(f"history unreadable: {e}") from e
    out = []
    for l in lines:
        if not l.strip():
            continue
        try:
            e = json.loads(l)
        except ValueError as err:
            raise Refused("history has a line that is not JSON") from err
        if isinstance(e, dict) and e.get("study") == name:
            out.append(e)
    return out


def _salt(v):
    return v if isinstance(v, str) else None


def _ran(events, current, now, stopped=None):
    """The windows that ran: [{"start", "end", "salt", "stop"}], one per time a window was on, never merged (review
    4eaf0b6 I1: the same window written twice is two runs, and the second ran even if the first was replaced before its
    start). From HISTORY (each write, open until the next line, and the window each write replaced or off stopped,
    hand-written ones too), the switch as it is now and its stopped record."""
    runs = []

    def add(w, stop):
        if isinstance(w, dict) and _epoch(w.get("start")) and _epoch(w.get("end")) and _epoch(stop):
            runs.append({"start": w["start"], "end": w["end"], "salt": _salt(w.get("salt")), "stop": min(stop, w["end"])})
            return runs[-1]
        return None

    open_ = None
    mark = 0
    for e in events:
        at = e.get("at")
        if open_ is not None and _epoch(at):
            if e.get("k") == "abort" and e.get("published") is False:
                del runs[mark:]    # never on file: neither its window nor the one it said it replaced changed (M-a)
            else:
                open_["stop"] = min(open_["stop"], at)
            open_ = None
        if e.get("k") == "write":
            mark = len(runs)
        add(e.get("replaces"), at)
        add(e.get("window"), at)
        if isinstance(e.get("replaced_stopped"), dict):
            add(e["replaced_stopped"], e["replaced_stopped"].get("stop"))
        if e.get("k") == "write":
            open_ = add(e, e.get("end"))
    if current is not None:
        add(current, current["end"])
    if stopped is not None:
        add(stopped, stopped["stop"])
    return [r for r in runs if r["start"] < min(r["stop"], now)]


def _unknown(events, old, window, stopped):
    """Whether what ran before is not known (review 4eaf0b6 N1): a switch is there with no window and no stopped record
    and this study has no HISTORY line, or the last off that found it so (unknown_past) has no write after it. An off
    that knew the past (none there, or a window) is a line that knows it. A write the next line aborted is no line: the
    switch was put back or never on file (review 5399207 M-b)."""
    events = [e for i, e in enumerate(events)
              if not (e.get("k") == "write" and i + 1 < len(events) and events[i + 1].get("k") == "abort")
              and not (e.get("k") == "abort" and i > 0 and events[i - 1].get("k") == "write")]
    unknown = False
    for e in events:
        if e.get("unknown_past"):
            unknown = True
        elif e.get("k") == "write":
            unknown = False
    return unknown or (old is not None and window is None and stopped is None and not events)


def _guard(md, name, path, start, salt, new_study):
    """The old switch bytes, or Refused: a running window, or a later window that would make one that ran longer."""
    old, window = _current(path)
    now = time.time()
    if window is not None and window["start"] <= now + SOON and now < window["end"]:
        raise Refused(f"a window is running or about to ({window['start']} to {window['end']}): it is not moved, cut "
                      "or extended")
    events = _history(md, name)
    last = max((e["at"] for e in events if _epoch(e.get("at"))), default=None)
    if last is not None and now < last:
        raise Refused(f"the clock ({now}) is behind the history ({last}): what is running is not known")   # F3
    stopped = _stopped(path)
    if _unknown(events, old, window, stopped) and not new_study:
        raise Refused("what ran before is not known (a switch without a window, a stopped record or history): a window "
                      "now is another study (--new-study)")
    for w in _ran(events, window, now, stopped):
        if STUDIES[name]["salt"]:
            if salt == w["salt"]:
                raise Refused(f"salt {salt!r} is a window that ran ({w['start']} to {w['end']}): the same arms again "
                              "would make that study longer; a new study needs a new salt")
            if start < w["end"] and not new_study:
                raise Refused(f"a window that ran ends at {w['end']}: a new study before then needs --new-study")
        else:
            if start < w["end"]:
                raise Refused(f"a window that ran ends at {w['end']}: the shadow starts again only after it")
            if not new_study:
                raise Refused("a window ran before: another shadow window is another study (--new-study)")
    return old


def _prepare(path, data):
    """A temporary file beside the switch with data written and synced, the switch's mode (0644 when new): the slow part,
    done before the last checks (GPT Pro mwa9krfw I-1)."""
    try:
        mode = os.stat(path).st_mode & 0o777
    except FileNotFoundError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".", dir=os.path.dirname(path) or ".")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return tmp


def _publish(tmp, path):
    os.replace(tmp, path)


def _replace(path, data):
    tmp = _prepare(path, data)
    try:
        _publish(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _log(md, event):
    """One HISTORY line; whether it was written."""
    try:
        os.makedirs(os.path.join(md, os.path.dirname(HISTORY)), exist_ok=True)
        with open(os.path.join(md, HISTORY), "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return True
    except (OSError, TypeError, ValueError):
        return False


def _lock(md, tries):
    """An fd holding the writers' lock, or None if another writer held it for all the tries (0.1 s apart). The market
    dir is never made: one that is not there is OSError (review 4948771 I1); the lock needs no write permission (N2)."""
    if not os.path.isdir(md):
        raise FileNotFoundError(errno.ENOENT, "the market dir is not there", md)
    try:
        os.mkdir(os.path.join(md, os.path.dirname(LOCK)))
    except FileExistsError:
        pass
    fd = os.open(os.path.join(md, LOCK), os.O_RDONLY | os.O_CREAT, 0o644)
    try:
        for _ in range(tries):
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                time.sleep(0.1)
    except BaseException:
        os.close(fd)                       # N1
        raise
    os.close(fd)
    return None


def write(md, name, start, end, salt=None, new_study=False):
    """Write the study's window; {"path", "body", "written"}, or Refused (nothing changed)."""
    s = _study(name)
    _check(s, start, end, salt)
    try:
        fd = _lock(md, 30)
    except OSError as e:
        raise Refused(f"the writers' lock cannot be taken: {e}") from e
    if fd is None:
        raise Refused("another writer holds the lock: try again")
    try:
        return _write(md, name, s, start, end, salt, new_study)
    finally:
        os.close(fd)


def _check(s, start, end, salt):
    if not _epoch(start) or not _epoch(end):
        raise Refused("start and end must be finite epochs")
    hours = (end - start) / 3600
    if not 0 < start:
        raise Refused("the start must be a positive epoch")
    if not (hours > 0 if s["min_hours"] == 0 else hours >= s["min_hours"]):
        raise Refused(f"window {hours:.4f} h is empty or under the study's minimum {s['min_hours']} h")
    if hours > s["max_hours"]:
        raise Refused(f"window {hours:.4f} h is over the study's maximum {s['max_hours']} h")
    if s["salt"]:
        if not isinstance(salt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", salt):
            raise Refused("this study needs a salt of 1-64 [A-Za-z0-9_-]")
    elif salt is not None:
        raise Refused("this study takes no salt")


def _write(md, name, s, start, end, salt, new_study):
    path = os.path.join(md, s["file"])
    old = _guard(md, name, path, start, salt, new_study)
    old_stopped = _stopped(path)
    if not start > time.time() + SOON:      # else the last check aborts a window HISTORY already has (review 5399207 M-a)
        raise Refused(f"the start must be more than {SOON}s after now: the file is written before its window")
    d = {"enabled": True, **({"salt": salt} if s["salt"] else {}), "start": start, "end": end}
    tmp = _prepare(path, (json.dumps(d) + "\n").encode())     # the slow write and fsync before the last checks (I-1)
    try:
        replaces = _current_window(old)
        # the HISTORY line first, so no reader sees a window HISTORY does not have (GPT Pro mwa9krfw I-2)
        if not _log(md, {"k": "write", "study": name, "at": time.time(), "start": start, "end": end,
                         "salt": salt, "new_study": bool(new_study),
                         **({"replaces": replaces} if replaces is not None else {}),
                         **({"replaced_stopped": old_stopped} if old_stopped is not None else {})}):
            raise Refused("the history would not take the line: nothing written")
        again, window = _current(path)             # the running check again just before the rename (F4, I-1)
        now = time.time()
        if again != old or not start > now or (window is not None and window["start"] <= now + SOON
                                               and now < window["end"]):
            _log(md, {"k": "abort", "study": name, "at": now, "published": False})
            raise Refused("the switch changed, a window started or the start passed while this was checked: "
                          "nothing written")
        _publish(tmp, path)
        tmp = None
    finally:
        if tmp is not None and os.path.exists(tmp):
            os.unlink(tmp)
    written = os.stat(path).st_mtime
    ok = written <= start and s["reads"](md, start)
    if not ok:
        back = "put back as it was"
        try:
            if old is None:
                os.unlink(path)
            else:
                _replace(path, old)
        except OSError as e:
            back = f"putting it back failed ({e}); the switch was removed, so it is off"
            try:
                os.unlink(path)        # no switch is off for every reader; a window without history never stays
            except FileNotFoundError:
                pass
            except OSError as e2:
                back = f"putting it back failed ({e}) and removing it failed ({e2}): run --off now"
                print(json.dumps({"warning": back}, ensure_ascii=False), file=sys.stderr)
        _log(md, {"k": "abort", "study": name, "at": time.time(), "published": True})   # its window ends here
        raise Refused(f"the study's reader would not take it (written {written}, start {start}); " + back)
    return {"path": path, "body": d, "written": written}


def off(md, name):
    """{"enabled": false} over the study's switch, at once, with the window it stops."""
    s = _study(name)
    path = os.path.join(md, s["file"])
    if not os.path.isdir(md):
        raise FileNotFoundError(errno.ENOENT, "the market dir is not there", md)
    try:
        fd = _lock(md, 50)         # off waits for a writer, then stops anyway: it is never refused
    except OSError:
        fd = None
    try:
        out = _off(md, name, path)
        if fd is None:             # a stalled writer may rename over it: wait for the lock and look again (4948771 M1)
            try:
                fd = _lock(md, 100)
            except OSError:
                fd = None
            if fd is not None and _current(path)[1] is not None:
                out = _off(md, name, path)
        return dict(out, locked=fd is not None)
    finally:
        if fd is not None:
            os.close(fd)


def _off(md, name, path):
    old, window = _current(path)
    stopped = _stopped(path)
    try:
        events = _history(md, name)
    except Refused:
        events = None
    # history that cannot be read knows nothing: the past stays unknown after it is mended (adversarial e0a5379 F1)
    unknown = events is None or _unknown(events, old, window, stopped)
    at = time.time()
    d = {"enabled": False}
    if window is not None:
        d["stopped"] = {"start": window["start"], "end": window["end"], "at": at,
                        **({"salt": window["salt"]} if isinstance(window.get("salt"), str) else {})}
    elif stopped is not None:              # a second off keeps the record of the first (review 4eaf0b6 M1)
        d["stopped"] = {"start": stopped["start"], "end": stopped["end"], "at": stopped["stop"],
                        **({"salt": stopped["salt"]} if stopped["salt"] is not None else {})}
    _replace(path, (json.dumps(d) + "\n").encode())
    logged = _log(md, {"k": "off", "study": name, "at": time.time(),
                       **({"window": d["stopped"]} if window is not None else {}),
                       **({"unknown_past": True} if unknown else {})})
    return {"path": path, "body": d, "written": os.stat(path).st_mtime, "history": logged}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--market-dir", default=".market")
    p.add_argument("study", choices=sorted(STUDIES))
    p.add_argument("--start", type=float)
    p.add_argument("--end", type=float)
    p.add_argument("--salt")
    p.add_argument("--off", action="store_true")
    p.add_argument("--new-study", action="store_true", help="the window is another study after one that ran")
    a = p.parse_args(argv)
    try:
        out = off(a.market_dir, a.study) if a.off else write(a.market_dir, a.study, a.start, a.end, salt=a.salt,
                                                             new_study=a.new_study)
    except (Refused, OSError) as e:
        print(json.dumps({"refused": str(e)}, ensure_ascii=False))
        return 2
    print(json.dumps(out, ensure_ascii=False))
    if out.get("locked") is False:
        print(json.dumps({"warning": "off was written without the writers' lock: a writer still running could put a "
                                     "window back; run --off again"}), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
