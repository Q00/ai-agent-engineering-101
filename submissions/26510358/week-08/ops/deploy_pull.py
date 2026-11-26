"""Deploy week-08 from the server hub, run by a systemd user timer every 2 min (ops/install_deploy.sh).

Local agents push to the hub (~/git/ai-agent-engineering-101.git); this server only fast-forwards to what they pushed:
  1. fetch hub/week-08; nothing new -> exit. Server ahead (a commit made here) -> push it to the hub. Diverged -> stop.
  2. every changed path inside submissions/26510358/, else the commit is refused (marked bad, never retried)
  3. CI (ops/ci.py: scope, secrets, compile, lint, imports, pytest) on the new commit; the summary is attached to
     the commit in the hub as a git note (refs/notes/ci) so the agent that pushed it can read why it failed
  4. code changed (not only logs/, tests/ or *.md) -> wait out the restock window (3 min every 20 min),
     fast-forward, restart the unit, and wait for the shop and the buyer to be alive on the board again (HEALTH_S)
  5. then watch WATCH_S: new errors in the service log (known-benign ones excluded) against the same span before
     the deploy, a seller crash loop, and deals still settling for the shop and for the buyer
  6. any of 4-5 failing -> one revert commit of the deployed range (history kept: AGENTS.md invariant 4),
     pushed to the hub, unit restarted again; the bad commit is never retried
Every step goes to logs/deploy/deploy.jsonl. --dry-run reports what would happen and changes nothing.
"""
import argparse
import fcntl
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci                                                    # noqa: E402

WEEK08 = Path(__file__).resolve().parents[1]
REPO = WEEK08.parents[2]
REL = "submissions/26510358/week-08/"
SCOPE = "submissions/26510358/"
HUB, BRANCH = "hub", "week-08"
UNIT = "week08-myeonggi-market-v4.service"
NAME = "myeonggi"
MARKET = "https://market.ourolabs.ai"
HUB_DIR = Path.home() / "git" / "ai-agent-engineering-101.git"
CONSOLE = WEEK08 / "logs" / "round3-v4-join-20261103T013524Z" / "runtime-console.log"   # the unit's stdout
STATE = WEEK08 / ".market" / "deploy-state.json"
LOG = WEEK08 / "logs" / "deploy" / "deploy.jsonl"
WINDOW_LEAD_S = 300            # a restart takes ~3 min: never start one within 5 min of a restock window
WINDOW_WAIT_S = 25 * 60
HEALTH_S = 600                 # shop and buyer alive again within 10 min (re-registration took ~3 min on 11-10)
WATCH_S = 20 * 60              # then 20 min of errors and deals (11-10: buyer ~60 deals/h, shop ~60/h)
ERR_MIN = 5                    # more new errors than max(ERR_MIN, 3 x the same span before the deploy) -> roll back
# 11-10, 12 h of the log: 70 errors, all the OpenTelemetry context-detach ValueError, harmless
BENIGN = re.compile(r"ContextVar name='current_context'|Failed to detach context")
ERROR_LINE = re.compile(r"^(\d\d):(\d\d):(\d\d) \[[\w-]+\] (?:[A-Za-z_.]*(?:Error|Exception)\b|your seller exited|watchdog: )")


def log(event, **kw):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts": round(time.time(), 1), "at": time.strftime("%m-%d %H:%M:%S"), "event": event, **kw}
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps(row, ensure_ascii=False), flush=True)


def git(*args, check=True):
    r = subprocess.run(["git", "-C", str(REPO), *args], capture_output=True, text=True)
    if check and r.returncode:
        raise RuntimeError(f"git {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
    return r.stdout.strip() if check else r


def state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"bad": {}, "noted": None}


def save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1))


def get_json(path, timeout=10):
    req = urllib.request.Request(MARKET + path, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def alive():
    """(shop alive, buyer alive) for our name on the board; (None, None) when the board does not answer."""
    try:
        b = get_json("/api/board")
    except Exception:
        return None, None
    pick = lambda rows: next((bool(x.get("alive")) for x in rows or [] if x.get("name") == NAME), False)
    return pick(b.get("shops")), pick(b.get("buyers"))


def window_clear():
    """True when no restock window is open or opens within WINDOW_LEAD_S, on the market's clock."""
    try:
        r = get_json("/api/restock")
    except Exception:
        return True                     # unknown: do not block a deploy forever on a missing endpoint
    now = r.get("now") or time.time()
    if r.get("open"):
        return False
    return not r.get("opens_at") or r["opens_at"] - now > WINDOW_LEAD_S or (r.get("closes_at") or 0) < now


def ci_note(sha, text):
    """Attach the CI summary to the commit in the hub (refs/notes/ci); agents read it with
    git fetch hub refs/notes/ci:refs/notes/ci && git notes --ref=ci show <sha>"""
    subprocess.run(["git", "--git-dir", str(HUB_DIR), "notes", "--ref=ci", "add", "-f", "-m", text, sha],
                   capture_output=True)


def restart_and_check(before):
    subprocess.run(["systemctl", "--user", "restart", UNIT], check=True, capture_output=True, timeout=60)
    t0, ok_streak = time.time(), 0
    while time.time() - t0 < HEALTH_S:
        time.sleep(30)
        active = subprocess.run(["systemctl", "--user", "is-active", UNIT], capture_output=True,
                                text=True).stdout.strip() == "active"
        shop, buyer = alive()
        if shop is None:                # board down: judge on the unit only
            shop, buyer = before
        # a role that was not alive before the restart is not held against the new code
        good = active and (shop or not before[0]) and (buyer or not before[1])
        ok_streak = ok_streak + 1 if good and time.time() - t0 >= 90 else 0
        if ok_streak >= 2:
            return True, round(time.time() - t0)
    return False, round(time.time() - t0)


def deals():
    """(shop deals, buyer deals) of our name on the board, or None."""
    try:
        b = get_json("/api/board")
    except Exception:
        return None
    pick = lambda rows: next((x.get("deals") or 0 for x in rows or [] if x.get("name") == NAME), 0)
    return pick(b.get("shops")), pick(b.get("buyers"))


def log_size():
    try:
        return CONSOLE.stat().st_size
    except OSError:
        return 0


def errors(text, since_s=None):
    """Error lines that matter in a piece of the service log; since_s keeps lines of the last since_s seconds
    (the log has only the time of day)."""
    now = time.localtime()
    now_s = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    out = []
    for ln in text.splitlines():
        m = ERROR_LINE.match(ln)
        if not m or BENIGN.search(ln):
            continue
        if since_s is not None:
            t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
            if (now_s - t) % 86400 > since_s:
                continue
        out.append(ln[:200])
    return out


def read_from(offset, limit=30_000_000):
    with CONSOLE.open("rb") as f:
        f.seek(max(offset, log_size() - limit))
        return f.read().decode("utf-8", "replace")


def watch(baseline):
    """WATCH_S after a healthy restart: (ok, why). Fails on new errors above max(ERR_MIN, 3 x baseline), a seller
    restarting twice, or no new deal for the shop or for the buyer."""
    off, d0, t0 = log_size(), deals(), time.time()
    limit = max(ERR_MIN, 3 * baseline)
    while time.time() - t0 < WATCH_S:
        time.sleep(60)
        new = errors(read_from(off))
        crashes = sum("your seller exited" in x for x in new)
        if len(new) > limit or crashes >= 2:
            return False, {"errors": len(new), "limit": limit, "crashes": crashes, "sample": new[-5:]}
    d1 = deals()
    new = errors(read_from(off))
    if d0 and d1 and (d1[0] <= d0[0] or d1[1] <= d0[1]):
        return False, {"deals_before": d0, "deals_after": d1, "why": "no new deal for the shop or the buyer"}
    return True, {"errors": len(new), "limit": limit, "deals_before": d0, "deals_after": d1}


def rollback(head, remote, s, why):
    """One revert commit of the range, pushed to the hub so every clone sees the failure and the fix."""
    git("revert", "--no-edit", "--no-commit", f"{head}..{remote}")
    git("commit", "-q", "-m", f"revert: auto-rollback {head[:8]}..{remote[:8]} ({why}; ops/deploy_pull.py)")
    git("push", "-q", HUB, f"HEAD:{BRANCH}")
    s["bad"][remote] = why
    save(s)
    ok2, secs2 = restart_and_check((True, True))
    log("rolled_back", to=git("rev-parse", "--short", "HEAD"), why=why, health_ok=ok2, seconds=secs2)


def needs_restart(paths):
    """Only code the service loads needs a restart: logs/, tests/ and *.md do not."""
    return any(p.startswith(REL) and not p.startswith((REL + "logs/", REL + "tests/")) and not p.endswith(".md")
               for p in paths)


def main(dry):
    if git("rev-parse", "--abbrev-ref", "HEAD") != BRANCH:
        log("stop", why=f"server checkout is not on {BRANCH}")
        return 1
    git("fetch", "-q", HUB, BRANCH)
    head, remote = git("rev-parse", "HEAD"), git("rev-parse", f"{HUB}/{BRANCH}")
    s = state()
    if head == remote:
        return 0
    if git("merge-base", "--is-ancestor", remote, head, check=False).returncode == 0:
        if not dry:
            git("push", "-q", HUB, f"HEAD:{BRANCH}")
        log("pushed_server_commits" + ("_dry" if dry else ""), head=head[:8], hub=remote[:8])
        return 0
    if git("merge-base", "--is-ancestor", head, remote, check=False).returncode != 0:
        if s.get("noted") != remote:
            log("diverged", head=head[:8], hub=remote[:8], why="server and hub both have new commits: merge by hand")
            s["noted"] = remote
            save(s)
        return 1
    if remote in s["bad"]:
        return 0
    paths = git("diff", "--name-only", head, remote).splitlines()
    commits = git("log", "--format=%h %s", f"{head}..{remote}").splitlines()
    outside = [p for p in paths if not p.startswith(SCOPE)]
    if outside:
        log("refused", hub=remote[:8], why="paths outside " + SCOPE, paths=outside[:10])
        s["bad"][remote] = "outside scope"
        save(s)
        return 1
    restart = needs_restart(paths)
    log("new_commits", head=head[:8], hub=remote[:8], n=len(commits), commits=commits[:10], restart=restart)
    rep = ci.run(REPO, head, remote)
    ci_note(remote, ci.summary(rep))
    log("ci", hub=remote[:8], ok=rep["ok"], seconds=rep["seconds"], config=rep["config_changes"],
        failed={c["name"]: c["detail"][-600:] for c in rep["checks"] if not c["ok"]})
    if not rep["ok"]:
        s["bad"][remote] = "ci failed: " + ",".join(c["name"] for c in rep["checks"] if not c["ok"])
        save(s)
        return 1
    if dry:
        log("dry_run_stop", hub=remote[:8], would_restart=restart, window_clear=window_clear())
        return 0
    if restart:
        t0 = time.time()
        while not window_clear():
            if time.time() - t0 > WINDOW_WAIT_S:
                log("window_wait_timeout", hub=remote[:8])
                return 1
            time.sleep(15)
    before = alive()
    baseline = len(errors(read_from(max(0, log_size() - 30_000_000)), since_s=WATCH_S))
    git("merge", "-q", "--ff-only", remote)
    log("fast_forwarded", head=remote[:8])
    if not restart:
        return 0
    ok, secs = restart_and_check(before if before[0] is not None else (True, True))
    log("health", hub=remote[:8], ok=ok, seconds=secs, before=before, after=alive())
    if not ok:
        rollback(head, remote, s, f"shop/buyer not alive on the board {HEALTH_S // 60} min after restart")
        return 1
    ok, info = watch(baseline)
    log("watch", hub=remote[:8], ok=ok, baseline_errors=baseline, **info)
    if not ok:
        rollback(head, remote, s, "post-deploy watch failed: " + (info.get("why") or f"{info.get('errors')} errors, "
                                                                   f"{info.get('crashes', 0)} seller restarts"))
        return 1
    ci_note(remote, ci.summary(rep) + f"\nDEPLOYED {time.strftime('%m-%d %H:%M')}: healthy, watch ok "
            f"({info.get('errors')} new errors, deals {info.get('deals_before')} -> {info.get('deals_after')})")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    lock = open(WEEK08 / ".market" / "deploy.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)                     # a deploy (window wait, health check) is still running
    try:
        sys.exit(main(a.dry_run))
    except Exception as e:
        log("error", why=f"{type(e).__name__}: {str(e)[:300]}")
        sys.exit(1)
