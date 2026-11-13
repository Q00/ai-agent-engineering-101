"""Operations (design 9, 17-4, R9, R13): ops.health, ops.emergency, model.usage.

    py -m harness.ops                        # one health pass (the runner does it every 5 min)
    py -m harness.ops emergency              # what is stopped
    py -m harness.ops clear restock_stop     # the user undoes an emergency action

Health: the measured findings of outer/health.py plus whether the parts are alive (the kit log, the inner status),
graded now / warn; one alert per finding and hour.
Emergency (17-4, approved in advance, at night too): restock_stop (no automatic trigger since 11/6 23:2x: a rules
change only alerts), forum_stop when the
forum turn keeps failing. An action is a flag in memory/bus/emergency.json that the role reads (restock.py, the
runner's forum turn); it never changes prices or the strategy, it alerts at once, and only the user clears it.
Model usage (R13, 17-5): calls per seat in the last hour against the budget; 80% is a warning.
"""
import json
import sys
import time

from core import atomic, paths
from harness import alert

ACTIONS = ("restock_stop", "sku_stop", "buyer_pause", "forum_stop", "models_stop")     # 17-4
NOW_FINDINGS = ("the shop sold nothing", "the buyer paid nothing")                  # the rest are warnings
# 17-5; strategy 2 -> 16 (user 11/6 10:3x): the meta-harness proposes in the tool loop, up to 8 calls a cycle
# user 11/6 11:0x: meta every 30 min -> 2 cycles an hour: strategy up to 16 calls, analyst and audit 2 each
BUDGET_PER_HOUR = {"seller": 30, "buyer": 30, "analyst": 12, "strategy": 24, "audit": 6, "forum": 0}
STALE_S = 600


def _state_path():
    return paths.memory() / "harness" / "ops_state.json"


def emergency_path():
    return paths.bus() / "emergency.json"


def emergency():
    return atomic.read_json(emergency_path(), default=None) or {}


def stopped(action):
    """For the roles: is this pre-approved emergency action in force?"""
    return bool(emergency().get(action))


def trigger(action, why, now=None):
    if action not in ACTIONS:
        raise ValueError(f"not a pre-approved action: {action}")
    now = now or time.time()
    e = emergency()
    if e.get(action):
        return False
    e[action] = {"since": now, "why": why}
    atomic.write_json(emergency_path(), e)
    alert.send(f"[비상 동작] {action}: {why}\n되돌리기: py -m harness.ops clear {action}", "now", now)
    return True


def clear(action, now=None):
    e = emergency()
    was = e.pop(action, None)
    atomic.write_json(emergency_path(), e)
    if was:
        alert.send(f"[해제] {action} (사람이 되돌림)", "page", now)
    return was


def model_usage(now=None):
    """model.usage: calls per seat in the last hour, and the share of the hourly budget."""
    now = now or time.time()
    from harness.trajectory import read_day
    days = sorted({time.strftime("%Y%m%d", time.localtime(t)) for t in (now - 3600, now)})
    n = {}
    for d in days:
        for e in read_day(d):
            if e["kind"] == "model.call" and now - 3600 <= e["ts"] <= now:
                seat = (e.get("data") or {}).get("seat") or "?"
                n[seat] = n.get(seat, 0) + 1
    return {s: {"calls": c, "budget": BUDGET_PER_HOUR.get(s),
                "share": round(c / BUDGET_PER_HOUR[s], 2) if BUDGET_PER_HOUR.get(s) else None} for s, c in n.items()}


QUIET_S = 600       # no buyer at our seller this long -> probe the shop's public address (user 11/7 13:3x)


def _kit_tail(nbytes=400_000):
    kit = paths.root() / "logs" / "market-kit.log"
    try:
        with open(kit, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(0, fh.tell() - nbytes))
            return fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def shop_reach(now=None, probe=None):
    """(level, finding) or None. 11/7 12:58-13:30 the tunnel stopped answering: the seller was alive and reachable
    locally, but no buyer came in for 32 min until the kit noticed. When the kit log shows no seller turn for
    QUIET_S, the public address the shop last registered is asked for its agent card."""
    import re
    import urllib.request
    now = now or time.time()
    lines = _kit_tail()
    url, last = None, None
    for line in lines:
        m = re.search(r"shop registered (?:again )?as \S+ at (https://\S+)|\[tunnel\] (https://\S+?),? waiting", line)
        if m:
            url = m.group(1) or m.group(2)
        if " [seller] [seller] " in line[:30] or line[9:27] == "[seller] [seller] ":
            last = line[:8]
    if last is None:
        return None
    # 11/11: 'now' is the runner pass's start, and the kit log is read minutes later - a turn written after 'now'
    # was taken for yesterday's ('quiet for 1438 min', three false alerts); the log is compared with the clock
    now = max(now, time.time())
    t = time.localtime(now)
    h, mi, se = (int(x) for x in last.split(":"))
    ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, se, 0, 0, -1))
    if ts > now + 3600:
        ts -= 86400                                      # the last turn was before midnight
    quiet = now - ts
    if quiet < QUIET_S:
        return None
    ok = False
    if url:
        try:
            if probe is not None:
                ok = probe(url)
            else:
                with urllib.request.urlopen(url.rstrip("/") + "/.well-known/agent-card.json", timeout=10) as r:
                    ok = r.status == 200
        except Exception:
            ok = False
    mins = int(quiet // 60)
    if not ok:
        return ("now", f"shop unreachable: {url or 'no public address in the kit log'} does not answer and no buyer "
                       f"reached the seller for {mins} min (tunnel down? the kit reopens it when it notices)")
    return ("warn", f"no buyer at the seller for {mins} min, though {url} answers")


REVIVE_GAP_S = 900


def _ps(cmd):
    import subprocess
    r = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def revive_kit(now, st):
    """Start the kit again when its log is stale and no kit process is alive (11/9 14:32 the kit exited without a
    word and stayed down 24 h: the alerts were not sent - no Telegram chat set - and nothing restarted it).
    A kit that is alive but silent is left to the alert (never two kits at once). At most once per REVIVE_GAP_S."""
    import os
    import subprocess
    if os.name != "nt" or now - st.get("revived", 0) < REVIVE_GAP_S:
        return None
    alive = _ps("@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'market_kit\\.py' }).Count")
    if alive not in ("0", ""):
        return None
    # the old kit's seller and tunnel outlive it and serve nobody
    _ps("Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'seller\\.py|market_buyer|cloudflared' }"
        " | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    st["revived"] = now
    env = dict(os.environ, PYLAUNCHER_NO_SEARCH_PATH="1")
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File",
                      str(paths.root() / "ops" / "inner.ps1")], env=env, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS)
    return ("now", "the kit was not running: started ops/inner.ps1 again")


def checkout_refusals(now=None, window_s=3600):
    """(count, {unit: n}) of our checkout's refusals in the last hour from the kit log (11/12: 331 deals agreed and
    refused at payment over 15 h - 'unit ... is not in stock for this order' - and nothing in the harness counted it)."""
    import re
    now = max(now or 0, time.time())
    t = time.localtime(now)
    n, units = 0, {}
    for line in _kit_tail(1_500_000):
        if "[acp] checkout refused" not in line:
            continue
        try:
            h, mi, se = (int(x) for x in line[:8].split(":"))
        except ValueError:
            continue
        ts = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, h, mi, se, 0, 0, -1))
        if ts > now + 3600:
            ts -= 86400
        if now - ts > window_s:
            continue
        n += 1
        m = re.search(r"unit (u-[0-9a-f]+)", line)
        if m:
            units[m.group(1)] = units.get(m.group(1), 0) + 1
    return n, units


def agreed_unsettled(now=None, window_s=3600):
    """{kind: (agreed, settled)} of our shop in the last hour: seller.closed completed vs seller.settled. 11/13: goods
    receipts without a warranty were agreed 125 times in 8 h and settled 18 - the buyers' kits did not pay, and nothing
    in our logs refused, so no check saw it until the user asked."""
    from core import index
    now = now or time.time()
    cat = atomic.read_json(paths.root() / ".market" / "catalog.json", default=None) or {}
    cat = cat.get("items", cat) if isinstance(cat, dict) else {c["sku"]: c for c in cat}
    out = {}
    for kind, i in (("seller.closed", 0), ("seller.settled", 1)):
        for e in index.events([kind], since=now - window_s):
            d = e["data"]
            if e["ts"] < now - window_s or (i == 0 and d.get("outcome") != "completed"):
                continue
            k = (cat.get(d.get("sku")) or {}).get("kind") or "?"
            out.setdefault(k, [0, 0])[i] += 1
    return {k: tuple(v) for k, v in out.items()}


def health(now=None, rules_changed=False):
    """ops.health: findings with their level; alerts once per finding and hour; emergency actions when due."""
    now = now or time.time()
    from outer import health as h
    doc = h.check(now)
    found = [("now" if f.startswith(NOW_FINDINGS) else "warn", f) for f in doc["findings"]]
    kit = paths.root() / "logs" / "market-kit.log"
    if kit.exists() and now - kit.stat().st_mtime > STALE_S:
        found.append(("now", f"the kit log has not moved for {int((now - kit.stat().st_mtime) // 60)} min"))
    inner = paths.bus() / "status" / "inner.json"
    if inner.exists() and now - inner.stat().st_mtime > STALE_S:
        found.append(("now", f"the inner runtime's status is {int((now - inner.stat().st_mtime) // 60)} min old"))
    try:
        n, units = checkout_refusals(now)
        if n >= 5:
            top = sorted(units.items(), key=lambda kv: -kv[1])[:3]
            found.append(("now", f"our checkout refused {n} agreed deals in the last hour (units {top}): the seller "
                                 "promises units the kit will not ship"))
    except Exception as e:
        found.append(("warn", f"checkout refusal count failed: {type(e).__name__}"))
    try:
        for k, (agreed, settled) in agreed_unsettled(now).items():
            if agreed >= 10 and settled < 0.5 * agreed:
                found.append(("now", f"our shop settled {settled} of {agreed} agreed {k} deals in the last hour: buyers "
                                     "agree and do not pay (what the receipt promises, e.g. the warranty?)"))
    except Exception as e:
        found.append(("warn", f"agreed/settled check failed: {type(e).__name__}"))
    try:
        reach = shop_reach(now)
        if reach:
            found.append(reach)
    except Exception as e:                          # a probe never stops the health pass
        found.append(("warn", f"shop reach check failed: {type(e).__name__}: {e}"[:200]))
    for seat, u in model_usage(now).items():
        if u["share"] is not None and u["share"] >= 0.8:
            found.append(("warn", f"model seat {seat}: {u['calls']} calls in 1 h, {u['share']:.0%} of the budget"))
    st = atomic.read_json(_state_path(), default=None) or {}
    if kit.exists() and now - kit.stat().st_mtime > STALE_S:
        try:
            revived = revive_kit(now, st)
            if revived:
                found.append(revived)
        except Exception as e:
            found.append(("now", f"kit restart failed: {type(e).__name__}: {e}"[:200]))
    sent = st.setdefault("alerted", {})
    for level, f in found:
        key = f.split(":")[0][:40]
        if now - sent.get(key, 0) > 3600:
            alert.send(f"[{'긴급' if level == 'now' else '경고'}] {f}", level, now)
            sent[key] = now
    if rules_changed:
        # user 11/6 23:2x: a rules change no longer stops restock - patch 3.5 (forum votes) stopped all buying for
        # 5 h unseen (the phone alert was not sent); the change is an alert, the user reads it and decides
        alert.send("[규칙 변경] the market's rules changed: read memory/market/rules (restock keeps running)", "now", now)
    atomic.write_json(_state_path(), st)
    atomic.write_json(paths.memory() / "harness" / "health_now.json", {"ts": now, "findings": found})
    return {"findings": found, "emergency": emergency()}


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["emergency"]:
        print(json.dumps(emergency(), ensure_ascii=False, indent=1))
    elif a[:1] == ["clear"]:
        print(json.dumps(clear(a[1]), ensure_ascii=False))
    else:
        print(json.dumps(health(), ensure_ascii=False, indent=1, default=str))
