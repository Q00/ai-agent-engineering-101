#!/usr/bin/env python3
# v121c post-restart check (CR v121b R3/U3 = r3h U3 fix). READ-ONLY: local files only, no network, writes nothing.
# Exit 0 all PASS, 1 a FAIL, 2 PENDING (not enough deals yet). Non-gating in call_v121_go.sh (summary only).
# r3h U3: the market writes settle data.shop = our tunnel URL (not a name) and settle rows carry own_owner=true, so our
# settles are matched by URL (--own-url, repeatable) OR own_owner OR the ledger # / unit numbers seen in the kit log
# ([acp] settled <item> at <price>, ledger #N, unit u-xxxx); re-registration is judged ONLY on kit-log lines after
# --since (HH:MM:SS lines mapped to box-local epochs by a backward scan; a later time going backwards = previous day).
import argparse
import datetime
import json
import re
import sys
import time
from pathlib import Path

REG = "lee-sungsoo"
TS_RX = re.compile(r"^\[?(\d{2}):(\d{2}):(\d{2})")
SETTLED_RX = re.compile(r"\[acp\] settled .*?ledger #(\d+)(?:, unit (u-[0-9a-zA-Z]+))?")
RES = []


def check(name, ok, detail):
    st = "PENDING" if ok is None else ("PASS" if ok else "FAIL")
    RES.append(st)
    print(f"[{st}] {name}: {detail}")


def norm_url(u):
    return re.sub(r"^https?://", "", str(u or "").strip().lower()).rstrip("/")


def kit_lines_after(path, since, now=None):
    # -> [(epoch, line)] for lines at or after since. Backward scan: start from today (box local) at the last line.
    lines = Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    now = time.time() if now is None else now
    day = datetime.datetime.fromtimestamp(now).date()
    out, prev, cur = [], None, None
    for ln in reversed(lines):
        m = TS_RX.match(ln)
        if m:
            sec = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
            if prev is not None and sec > prev + 60:
                day = day - datetime.timedelta(days=1)
            prev = sec
            cur = datetime.datetime.combine(day, datetime.time()).timestamp() + sec
        if cur is None:
            continue
        if cur < since:
            break
        out.append((cur, ln))
    return list(reversed(out))


def main(argv=None):
    a = argparse.ArgumentParser()
    a.add_argument("--kit-log", required=True)
    a.add_argument("--since", required=True, type=float, help="restart epoch seconds")
    a.add_argument("--state", help="seller R3_STATE dir (events.jsonl)")
    a.add_argument("--own-url", action="append", default=[])
    a.add_argument("--pre-board")
    a.add_argument("--post-board")
    a.add_argument("--n", type=int, default=3)
    a.add_argument("--now", type=float)
    o = a.parse_args(argv)
    L = kit_lines_after(o.kit_log, o.since, o.now)
    reg = [ln for _, ln in L if "[market] shop registered as" in ln]
    check("kit re-registered under the same name (lines after --since only)",
          None if not reg else all(REG in ln for ln in reg),
          f"{len(reg)} registration lines after since" + (f", last: {reg[-1][-80:]}" if reg else " (none yet / kit did not re-register)"))
    led, units = set(), set()
    settled = refused = 0
    for _, ln in L:
        m = SETTLED_RX.search(ln)
        if m:
            settled += 1
            led.add(int(m.group(1)))
            if m.group(2):
                units.add(m.group(2))
        if "[acp] settle refused" in ln or "[acp] checkout refused" in ln:
            refused += 1
    check(f"kit log settled after restart >= {o.n}", None if settled < o.n else True,
          f"settled={settled} refused={refused} ledger#={sorted(led)[:10]}")
    if o.state:
        urls = {norm_url(u) for u in o.own_url if u}
        own = mine_false = 0
        how = {"url": 0, "own_owner": 0, "ledger_or_unit": 0}
        ev = Path(o.state) / "events.jsonl"
        for line in ev.read_text(encoding="utf-8", errors="replace").splitlines() if ev.exists() else []:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "settle" or (e.get("ts") or 0) < o.since:
                continue
            d = e.get("data") or {}
            by = ("url" if urls and norm_url(d.get("shop")) in urls else "own_owner" if d.get("own_owner") is True
                  else "ledger_or_unit" if (d.get("ledger") in led or d.get("unit") in units) else None)
            if by:
                own += 1
                how[by] += 1
                mine_false += not e.get("mine")
        check("local settle events of our shop since restart", None if own < o.n else True,
              f"{own} matched {how}; mine=false on {mine_false} (own_owner rows may be mine=false by design)")
    if o.pre_board and o.post_board:
        pre, post = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (o.pre_board, o.post_board))
        row = lambda b: next((r for r in b.get("shops", []) if r.get("name") == REG), None)
        s0, s1 = row(pre), row(post)
        if s0 and s1:
            dd = (s1.get("deals") or 0) - (s0.get("deals") or 0)
            check(f"board deals on {REG} +>= {o.n}", None if dd < o.n else True, f"+{dd}; card.name={(s1.get("card") or {}).get("name")!r}")
        else:
            check(f"board shop row {REG}", False, f"pre={bool(s0)} post={bool(s1)}")
    if "FAIL" in RES:
        print("REVERT (r3h/v121c): needs a CoS decision; then ROLLBACK_v121c.md = call_v121_rollback.sh by procedure A"
              " (restores the pre_v121 tgz, seller-only restart with est=0 checks; kit / tmux / buyer untouched).")
    return 1 if "FAIL" in RES else 2 if "PENDING" in RES else 0


if __name__ == "__main__":
    sys.exit(main())
