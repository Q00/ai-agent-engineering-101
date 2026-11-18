"""After the next seller restart (person's request 11/09 ~19:45), check that --strict-mcp-config is really on:
  - the new seller process (python seller_r3.py, started after this watcher) has no HARNESS_CMD in its environment
    (harness.py reads strict_mcp.json only when HARNESS_CMD is unset)
  - the harness calls it makes: the command lines of its child processes (cmd /c claude -p ...), sampled every
    SAMPLE_S for up to SAMPLE_MAX_S or until MIN_CALLS are seen, each must carry --strict-mcp-config
  - strict_mcp.json seller / since, and the seller's '[seller] [harness]' lines in the kit console since the restart
One Telegram (info if all hold, urgent if not), each command line in logs/strict_mcp_check.log. Read-only.
    python tools/strict_mcp_check.py [--now]     (--now: check the running seller at once, print only)
"""
import glob
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
os.chdir(HERE)
import procinfo as P  # noqa: E402

LOG = os.path.join(HERE, "logs", "strict_mcp_check.log")
POLL_S, SAMPLE_S, SAMPLE_MAX_S, MIN_CALLS = 30, 0.3, 1800, 5
FLAG = "--strict-mcp-config"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def sellers():
    """[(pid, start)] of python processes running seller_r3.py."""
    out = []
    for pid, (_, exe) in P.processes().items():
        if exe.lower() != "python.exe":
            continue
        try:
            if "seller_r3.py" in P.cmdline(pid):
                out.append((pid, P.started(pid)))
        except OSError:
            pass
    return out


def sample(roots, until, min_calls=MIN_CALLS):
    """{child pid: command line} of harness calls (claude -p) under the seller processes."""
    seen = {}
    while time.time() < until and len(seen) < min_calls:
        procs = P.processes()
        for r in roots:
            for k in P.descendants(r, procs):
                if k in seen:
                    continue
                try:
                    c = P.cmdline(k)
                except OSError:
                    continue
                if "claude" in c and " -p" in c and "cmd.exe" not in c.lower():      # the claude process, not its cmd /c wrapper
                    seen[k] = c
        time.sleep(SAMPLE_S)
    return seen


def harness_lines(t0):
    f = sorted(glob.glob(os.path.join(HERE, "logs", "kit-console-*.txt")), key=os.path.getmtime)[-1]
    from strict_mcp_rollout import timed_lines
    return sum(1 for _ in timed_lines(f, "[seller] [harness] ", t0, time.time() + 60))


def check(group, t_restart):
    pids = [p for p, _ in group]
    env = {}
    for p in pids:
        try:
            env[p] = "HARNESS_CMD" in P.environ(p)
        except OSError as e:
            env[p] = f"read failed {e}"
    calls = sample(pids, time.time() + SAMPLE_MAX_S)
    for k, c in calls.items():
        log(f"call pid {k}: {c}")
    try:
        sm = json.load(open(os.path.join(HERE, "strict_mcp.json"), encoding="utf-8"))
    except (OSError, ValueError):
        sm = {}
    since_s = f"{datetime.fromtimestamp(sm['since']):%m-%d %H:%M}" if sm.get("since") else "-"
    flagged = sum(1 for c in calls.values() if FLAG in c)
    try:
        hl = harness_lines(t_restart)
    except Exception as e:
        hl = f"계산 실패 {type(e).__name__}"
    ok = calls and flagged == len(calls) and not any(v is True or isinstance(v, str) for v in env.values()) \
        and sm.get("seller") and time.time() >= (sm.get("since") or 0)
    text = (f"seller 재시작 {datetime.fromtimestamp(t_restart):%m-%d %H:%M:%S} 후 strict_mcp 확인: "
            + ("적용됨" if ok else "확인 필요") + "\n"
            f"- seller 프로세스 환경 HARNESS_CMD: " + ", ".join(f"pid {p} {'설정됨' if v is True else '없음' if v is False else v}"
                                                         for p, v in env.items()) + "\n"
            f"- seller가 띄운 harness 호출 {len(calls)}건 중 {FLAG} 포함 {flagged}건 (프로세스 명령줄, 최대 "
            f"{SAMPLE_MAX_S // 60}분 관찰)\n"
            f"- strict_mcp.json seller {sm.get('seller')}, since {since_s}")
    text += f"\n- kit 로그 '[seller] [harness]' 줄 (재시작 후): {hl}"
    return ok, text


def main():
    if "--now" in sys.argv:
        g = sellers()
        print("sellers", [(p, datetime.fromtimestamp(t).strftime("%m-%d %H:%M:%S")) for p, t in g])
        global SAMPLE_MAX_S
        SAMPLE_MAX_S = 120
        print(check(g, min(t for _, t in g))[1])
        return
    import alerts
    from limits import ROUND_END
    t0 = time.time()
    old = sellers()
    log(f"start; running sellers {[(p, round(t)) for p, t in old]}")
    while time.time() < ROUND_END:
        new = [(p, t) for p, t in sellers() if t > t0]
        if new:
            time.sleep(10)                                     # the launcher chain settles
            new = [(p, t) for p, t in sellers() if t > t0]
            tr = min(t for _, t in new)
            log(f"seller restart seen: {[(p, round(t)) for p, t in new]}")
            ok, text = check(new, tr)
            alerts.send("info" if ok else "urgent", f"strict_mcp_check:{int(tr)}", text, log=log)
            log(f"sent ok={ok}")
            return
        time.sleep(POLL_S)


if __name__ == "__main__":
    main()
