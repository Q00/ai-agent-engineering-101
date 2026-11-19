"""Fast watch of the market's rules (user 11-08: players have settled, so a quick answer to rule updates matters).

    python rules_fast.py                 one check
    python rules_fast.py --loop 13 90    check every 90 s for 13 min (watch_round.sh starts this each 15 min tick,
                                         so the rules are read about every 1.5 min without a new cron line)

A change (memory_curator.rules_watch: the rules page and agent.md, hashed, with the changed lines) is
  1. pushed to the user at once (notify.py) with the first changed lines,
  2. queued for the curator (pending_rules in its watch state, kept until a digest has taken it in),
  3. answered by a forced curator run now, which rewrites the digest with rules_change and wakes both strategists.
Only one loop runs at a time (a lock file); a check takes ~1 s.
"""
import fcntl
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOCK = HERE / ".market" / "rules_fast.lock"
LOG = HERE / "logs" / "rules_fast.log"


def log(msg):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S UTC ", time.gmtime()) + msg + "\n")


def check():
    import memory_curator as mc
    changed = mc.rules_watch()
    if not changed:
        return False
    st = mc.read_json(mc.WATCH_STATE, {})
    st["pending_rules"] = (st.get("pending_rules") or {}) | changed
    mc.WATCH_STATE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    first = "\n".join(line for d in changed.values() for line in d.splitlines()[:12])[:900]
    if "--dry" in sys.argv:                              # test: report, no push, no curator run
        print("would push:", first[:300])
        return True
    log(f"RULES CHANGED: {list(changed)}; curator forced")
    subprocess.run([sys.executable, str(HERE / "notify.py"), "[yms979] 마켓 규칙 변경 감지",
                    f"바뀐 페이지: {', '.join(changed)}\n\n{first}\n\n큐레이터가 지금 분석하고 전략 에이전트들을 깨웁니다."],
                   capture_output=True, text=True)
    subprocess.Popen([sys.executable, str(HERE / "memory_curator.py"), "--force"], cwd=HERE,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return True


def main():
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        minutes, every = float(sys.argv[i + 1]), float(sys.argv[i + 2])
    else:
        minutes, every = 0, 0
    LOCK.parent.mkdir(exist_ok=True)
    with open(LOCK, "w") as lk:
        try:
            fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return                                       # another loop is running
        end = time.time() + minutes * 60
        while True:
            try:
                check()
            except Exception as e:
                log(f"check failed: {e!r}")
            if time.time() + every >= end:
                break
            time.sleep(every)


if __name__ == "__main__":
    main()
