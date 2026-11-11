"""Book-lead guard for the shop's warranty (user 11-07: a lead that gets caught is worth nothing).

    python shop_guard.py          one check (watch_round.sh runs it every 15 min)
    python shop_guard.py --dry    print what it would decide, change nothing

The warranty on goods (params.json shop.base: grades A and B, 72 h) costs refunds out of the book, the 0.5 part
of shop ALL, where we lead. Each check reads the board, keeps the lead over the next shop's book (24 h history in
.market/warranty_guard.json) and its slope over the last 4 h, and sets a level:

    2  A and B carry the warranty (params as they are)
    1  A only                                          when the lead < LEAD_B or it shrinks fast (< SHRINK_B)
    0  no warranty                                     when the lead < LEAD_ALL or it would be gone in 24 h

Going back up is one step at a time and needs a growing lead above a higher bar (hysteresis), so the level does
not flap. policy.shop_params() and seller_r3's card apply the level's grades (a stale guard, > 2 h, is ignored).
A level change is logged to logs/shop_guard.log, sent to the user through notify.py, and the kit is restarted
right after the next supply window closes so the agent card states the warranty the deals carry.
"""
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
GUARD = HERE / ".market" / "warranty_guard.json"
LOG = HERE / "logs" / "shop_guard.log"
NAME = "yms979"
LEAD_ALL, LEAD_B = 30e6, 60e6          # lowering bars
UP_TO_A, UP_TO_B = 60e6, 90e6          # raising bars (with a growing lead)
SHRINK_B = -1.5e6                      # 11-08: B goes off only on a clear shrink; -0.2M/h flipped it 5 times a night
GROW_UP = 1.0e6                        # and comes back only on a clear growth
HOLD_UP = 2 * 3600                     # held this long at a level before going back up
SLOPE_HOURS, KEEP = 4, 24 * 3600    # 11-08: back to 4 h (user); the 2 h slope was too jumpy and fired the last resort
GRADES = {2: ["A", "B", "C"], 1: ["A"], 0: []}     # 11-08: C carries the warranty too (user)


def log(msg):
    LOG.parent.mkdir(exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S UTC ", time.gmtime()) + msg + "\n")


def read():
    try:
        return json.loads(GUARD.read_text())
    except (OSError, ValueError):
        return {}


def grades_now(base_grades):
    """The warranty grades to use: base_grades cut to the guard's level (a stale or missing guard cuts nothing)."""
    g = read()
    if not g or time.time() - g.get("t", 0) > 2 * 3600:
        return list(base_grades)
    return [x for x in base_grades if x in GRADES.get(g.get("level", 2), [])]


def slope(hist, now):
    pts = [(t, v) for t, v in hist if now - t <= SLOPE_HOURS * 3600]
    if len(pts) < 2 or pts[-1][0] - pts[0][0] < 3600:
        return None
    return (pts[-1][1] - pts[0][1]) / ((pts[-1][0] - pts[0][0]) / 3600)     # credits per hour


def decide(level, lead, s, held=1e9):
    # 11-08: the projection test ('gone within 24 h at this slope') fired at a 157M lead on a 2 h slope of -7.5M/h and
    # switched every warranty off; a short slope is too jumpy to act on. The last resort is now the lead itself
    gone_in_24h = False
    if lead < LEAD_ALL or gone_in_24h:
        return 0, f"lead {lead / 1e6:.0f}M" + (", gone within 24 h at this slope" if gone_in_24h else f" < {LEAD_ALL / 1e6:.0f}M")
    if level >= 1 and (lead < LEAD_B or (s is not None and s < SHRINK_B)):
        if level == 2:
            return 1, f"lead {lead / 1e6:.0f}M" + (f" < {LEAD_B / 1e6:.0f}M" if lead < LEAD_B else f" shrinking {s / 1e6:+.1f}M/h")
        return 1, "holding A only"
    if level == 0:
        if lead >= UP_TO_A and s is not None and s >= 0 and held >= HOLD_UP:
            return 1, f"lead {lead / 1e6:.0f}M and not shrinking: back to A"
        return 0, "holding no warranty"
    if level == 1:
        if lead >= UP_TO_B and s is not None and s > GROW_UP and held >= HOLD_UP:
            return 2, f"lead {lead / 1e6:.0f}M and growing {s / 1e6:+.1f}M/h: back to A and B"
        return 1, "holding A only"
    return 2, "lead safe"


def after_supply_window():
    """True right after a supply window closed (the next one is > 8 min away): the only time we restart the kit."""
    try:
        r = json.loads((HERE / ".market" / "inventory.json").read_text()).get("restock") or {}
    except (OSError, ValueError):
        return False
    return not r.get("open") and (r.get("opens_at") or 0) - time.time() > 8 * 60


def restart_kit():
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines()
    for line in out:
        pid, _, args = line.strip().partition(" ")
        if args.split(" ")[0].endswith("/python") and f"market_kit.py join --dir {HERE}" in args:
            os.kill(int(pid), signal.SIGTERM)          # run_market.sh starts it again
            return pid
    return None


def relist_if_delisted(me, dry=False):
    """11-11: the market marked our shop alive=False and dropped it from /api/shops (no buyer could find us for
    12 min) while our tunnel and seller answered, so the kit's watchdog (3 failed card checks) never re-joined.
    When the board says dead but the local seller answers, kill the kit's cloudflared child: the watchdog opens a
    new tunnel within ~3 min and joins again ('shop registered again'). At most once per 20 min."""
    rf = HERE / ".market" / "relist.json"
    try:
        st = json.loads(rf.read_text())
    except (OSError, ValueError):
        st = {}
    if me.get("alive", True):
        if st.get("dead_since"):
            st.pop("dead_since", None)
            rf.write_text(json.dumps(st))
        return None
    # 11-11: the kit's own watchdog re-joins within a minute when the board says dead; this tunnel swap is the
    # fallback, used only when the shop has been dead for two consecutive ticks (>= 15 min)
    if not st.get("dead_since"):
        st["dead_since"] = time.time()
        rf.write_text(json.dumps(st))
        return "relist: board alive=False, first sighting (the kit watchdog re-joins by itself; acting if still dead next tick)"
    if time.time() - st["dead_since"] < 14 * 60:
        return "relist: board alive=False, waiting for the kit watchdog"
    if time.time() - st.get("relist_t", 0) < 20 * 60:
        return "relist: waiting (last try < 20 min ago)"
    out = subprocess.run(["ps", "-eo", "pid,ppid,args"], capture_output=True, text=True).stdout.splitlines()
    kit = next((l.split()[0] for l in out if f"market_kit.py join --dir {HERE}" in l and "/python" in l.split()[2]), None)
    if not kit:
        return "relist: no kit process found"
    tunnels = [l.split()[0] for l in out if len(l.split()) > 2 and l.split()[1] == kit and "cloudflared" in l]
    if not tunnels:
        return "relist: kit has no tunnel child"
    if not dry:
        for pid in tunnels:
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
        st["relist_t"] = time.time()
        rf.write_text(json.dumps(st))
        try:
            subprocess.run([sys.executable, str(HERE / "notify.py"), "[yms979] 상점 재등록",
                            "보드에서 상점이 꺼진 것으로 표시되어 터널을 교체하고 재등록을 유도했습니다 (3-4분 소요)."],
                           capture_output=True, text=True)
        except Exception:
            pass
    return f"relist: board alive=False, killed tunnel {tunnels} so the kit watchdog re-joins"


def main(dry=False):
    now = time.time()
    g = read()
    with urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30) as r:
        shops = [s for s in json.load(r)["shops"] if s.get("deals")]
    me = next(s for s in shops if s["name"] == NAME)
    try:
        msg = relist_if_delisted(me, dry)
        if msg:
            log(msg)
    except Exception as e:
        log(f"relist check failed: {e!r}")
    rival = max((s for s in shops if s["name"] != NAME), key=lambda s: s.get("book") or 0)
    lead = (me.get("book") or 0) - (rival.get("book") or 0)
    hist = [h for h in g.get("hist", []) if now - h[0] <= KEEP] + [[now, lead]]
    s = slope(hist, now)
    level = g.get("level", 2)
    new, why = decide(level, lead, s, now - g.get("since", 0))
    # 11-08 (user: 'warranty was not the problem, why change it? analyse the cause'): the guard read only the book
    # slope and toggled the warranty 5 times in a night while the real cause was settles refused for an 8760 h
    # warranty. It no longer moves the warranty, except as a last resort when the lead is nearly gone (level 0 at
    # < LEAD_ALL); otherwise it reports, and the shop strategist (opus) decides from the causes.
    if new != 0:
        if new != level:
            log(f"ALERT (not applied) {why}: the shop strategist decides")
        new, why = 2, "advisory only: " + why
    line = (f"book {me['book'] / 1e6:.0f}M vs {rival['name']} {rival['book'] / 1e6:.0f}M, lead {lead / 1e6:.0f}M, "
            f"slope {'n/a' if s is None else f'{s / 1e6:+.1f}M/h'} -> level {new} {GRADES[new]} ({why})")
    if dry:
        print(line)
        return
    pending = g.get("restart_pending", False) or new != level
    if new != level:
        log("CHANGE " + line)
        msg = (f"상점 보증 {GRADES[level] or '없음'} → {GRADES[new] or '없음'}\n장부 {me['book'] / 1e6:.0f}M vs "
               f"{rival['name']} {rival['book'] / 1e6:.0f}M, 격차 {lead / 1e6:.0f}M"
               + ("" if s is None else f", 시간당 {s / 1e6:+.1f}M") + f"\n이유: {why}")
        subprocess.run([sys.executable, str(HERE / "notify.py"), f"[yms979] 보증 단계 {level}→{new}", msg],
                       capture_output=True, text=True)
    else:
        log(line)
    if pending and after_supply_window():
        pid = restart_kit()
        log(f"restarted the kit (pid {pid}) after the supply window so the card states {GRADES[new] or 'no warranty'}")
        pending = False
    GUARD.write_text(json.dumps({"t": now, "since": now if new != level else g.get("since", now), "level": new, "grades": GRADES[new], "lead": lead, "slope": s,
                                 "rival": rival["name"], "why": why, "hist": hist, "restart_pending": pending}))


if __name__ == "__main__":
    main("--dry" in sys.argv)
