"""Deploy week-08 from the server hub, run by a systemd user timer every 2 min (ops/install_deploy.sh).

Local agents push to the hub (~/git/ai-agent-engineering-101.git); this server only fast-forwards to what they pushed:
  1. fetch hub/week-08; nothing new -> exit. Server ahead (a commit made here) -> push it to the hub. Diverged -> stop.
  2. every changed path inside submissions/26510358/, else the commit is refused (marked bad, never retried)
  3. pytest on the new commit in a sparse worktree (throwaway Ed25519 keys; the real keys never leave .market/*.pem)
  4. code changed (not only logs/ or *.md) -> wait out the restock window (3 min every 20 min), fast-forward,
     restart the unit, and wait for the shop and the buyer to be alive on the board again
  5. not alive within HEALTH_S -> one revert commit of the deployed range (history kept: AGENTS.md invariant 4),
     pushed to the hub, unit restarted again; the bad commit is never retried
Every step goes to logs/deploy/deploy.jsonl. --dry-run reports what would happen and changes nothing.
"""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

WEEK08 = Path(__file__).resolve().parents[1]
REPO = WEEK08.parents[2]
REL = "submissions/26510358/week-08/"
SCOPE = "submissions/26510358/"
HUB, BRANCH = "hub", "week-08"
UNIT = "week08-myeonggi-market-v4.service"
NAME = "myeonggi"
MARKET = "https://market.ourolabs.ai"
PYTEST = os.environ.get("DEPLOY_PYTEST", str(Path.home() / ".venvs/week08-qa/bin/pytest"))
STATE = WEEK08 / ".market" / "deploy-state.json"
LOG = WEEK08 / "logs" / "deploy" / "deploy.jsonl"
WINDOW_LEAD_S = 300            # a restart takes ~3 min: never start one within 5 min of a restock window
WINDOW_WAIT_S = 25 * 60
HEALTH_S = 600                 # shop and buyer alive again within 10 min (re-registration took ~3 min on 11-10)


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


def run_tests(sha):
    """pytest on sha in a sparse worktree (scripts/ + week-08 without logs/). Returns (ok, tail of the output)."""
    tmp = Path(tempfile.mkdtemp(prefix="deploy-wt-"))
    wt = tmp / "wt"
    try:
        git("worktree", "add", "--no-checkout", "--detach", str(wt), sha)
        subprocess.run(["git", "-C", str(wt), "sparse-checkout", "init", "--no-cone"], check=True, capture_output=True)
        sparse = subprocess.run(["git", "-C", str(wt), "rev-parse", "--git-path", "info/sparse-checkout"],
                                check=True, capture_output=True, text=True).stdout.strip()
        Path(wt, sparse).write_text(f"/scripts/\n/{REL}\n!/{REL}logs/\n")
        subprocess.run(["git", "-C", str(wt), "checkout", "-q", "--detach", sha], check=True, capture_output=True)
        w8 = wt / REL
        for k in ("shop_key.pem", "buyer_key.pem"):    # throwaway keys: tests sign receipts, the real keys stay put
            subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(w8 / k)], check=True,
                           capture_output=True)
        r = subprocess.run([PYTEST, "-q", "-p", "no:cacheprovider", "tests"], cwd=w8, capture_output=True,
                           text=True, timeout=1200)
        out = (r.stdout + r.stderr).strip().splitlines()
        return r.returncode == 0, "\n".join(x for x in out if "FAILED" in x or "passed" in x or "failed" in x
                                            or "error" in x.lower())[-1500:]
    finally:
        git("worktree", "remove", "--force", str(wt), check=False)
        shutil.rmtree(tmp, ignore_errors=True)
        git("worktree", "prune", check=False)


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


def needs_restart(paths):
    return any(p.startswith(REL) and not p.startswith(REL + "logs/") and not p.endswith(".md") for p in paths)


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
    ok, tail = run_tests(remote)
    log("tests", hub=remote[:8], ok=ok, out=tail)
    if not ok:
        s["bad"][remote] = "tests failed"
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
    git("merge", "-q", "--ff-only", remote)
    log("fast_forwarded", head=remote[:8])
    if not restart:
        return 0
    ok, secs = restart_and_check(before if before[0] is not None else (True, True))
    log("health", hub=remote[:8], ok=ok, seconds=secs, before=before, after=alive())
    if ok:
        return 0
    # roll back: one revert commit of the range, pushed to the hub so every clone sees the failure and the fix
    git("revert", "--no-edit", "--no-commit", f"{head}..{remote}")
    git("commit", "-q", "-m", f"revert: auto-rollback {head[:8]}..{remote[:8]} (shop/buyer not alive on the board "
        f"{HEALTH_S // 60} min after restart; ops/deploy_pull.py)")
    git("push", "-q", HUB, f"HEAD:{BRANCH}")
    s["bad"][remote] = "health check failed"
    save(s)
    ok2, secs2 = restart_and_check((True, True))
    log("rolled_back", to=git("rev-parse", "--short", "HEAD"), health_ok=ok2, seconds=secs2)
    return 1


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
