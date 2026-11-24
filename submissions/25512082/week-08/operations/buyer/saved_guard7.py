"""Buyer Saved 하락 방지 감시자 (2026-11-16 23:2x 승인, (b)안). buyer_ops7 코드는 바꾸지 않는다.

- 2분마다 공식 board에서 내 buyer Saved와, 학생 중 내 바로 아래 Saved를 읽는다.
- 경고: Saved <= 0.7900 이 되면 WARN 기록(한 번 넘을 때마다).
- 중단: Saved <= 0.7885 이거나, 바로 아래 학생과의 차이 <= 0.0004(백분위 하락 임박)이면
  stop_guard7.sh와 같은 방식으로 buyer_ops7 감독과 buyer 키트 프로세스 그룹에 SIGTERM을 보내 신규 주문을 멈춘다.
  자동 재시작은 없다. Seller, 가격 실험, 다른 안전 조건은 건드리지 않는다.
- board 조회 실패는 기록만 하고 다음 주기에 다시 본다(이 감시자 때문에 중단하지 않음).
- 11-17 18:00 KST에 도달하거나 buyer_ops7과 키트가 모두 끝나면 스스로 종료한다. LLM 호출 없음.
"""
import json, os, signal, subprocess, sys, time, urllib.request, datetime as dt
from pathlib import Path

HERE = Path(__file__).parent
LOG = HERE / "saved_guard7.log"
KST = dt.timezone(dt.timedelta(hours=9))
END_AT = dt.datetime(2026, 11, 17, 18, 0, tzinfo=KST).timestamp()
WARN_AT, STOP_AT, MIN_GAP = 0.7900, 0.7885, 0.0004
ME = "sohyunkim11"
KIT_PATTERN = f"market_kit.py join --dir {HERE}/agent"


def kst(t=None): return dt.datetime.fromtimestamp(t or time.time(), KST).strftime("%m-%d %H:%M:%S")
def log(kind, **kw):
    kw.update(t=kst(), kind=kind)
    with LOG.open("a") as f: f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def decide(saved, below):
    """순수 함수: ('stop'|'warn'|'ok', 사유)."""
    if round(saved, 4) <= STOP_AT: return "stop", f"Saved {saved} <= {STOP_AT}"
    if below is not None and round(saved - below, 4) <= MIN_GAP: return "stop", f"바로 아래 학생과 차이 {saved - below:.4f} <= {MIN_GAP}"
    if round(saved, 4) <= WARN_AT: return "warn", f"Saved {saved} <= {WARN_AT}"
    return "ok", ""


def board():
    b = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30))
    by = {y["name"]: y for y in b["buyers"]}
    stu = [by[p["buyer"]["name"]] for p in b["players"] if p.get("student") and p.get("buyer") and p["buyer"].get("name") in by]
    me = by[ME]; s = me["saved"]
    lower = [x["saved"] for x in stu if x["name"] != ME and x["saved"] < s]
    return s, (max(lower) if lower else None), me["deals"]


def _argv(pid):
    try: return Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore").split("\0")[:-1]
    except OSError: return []


def is_supervisor(a): return a == ["python3", "-I", "buyer_ops7.py"]
def is_kit(a): return any(x.endswith("market_kit.py") for x in a) and "join" in a and "--dir" in a and a[a.index("--dir") + 1] == f"{HERE}/agent"


def pids(kind):
    """kind: 'supervisor' | 'kit'. 명령줄 인자가 정확히 일치하는 프로세스만 고른다."""
    test = is_supervisor if kind == "supervisor" else is_kit
    return [int(p) for p in os.listdir("/proc") if p.isdigit() and int(p) != os.getpid() and test(_argv(p))]


def stop_buyer(reason):
    log("STOP", reason=reason)
    for p in pids("supervisor"):
        os.kill(p, signal.SIGTERM); log("sigterm", target="buyer_ops7", pid=p)
    time.sleep(1)
    for p in pids("kit"):
        try:
            os.killpg(os.getpgid(p), signal.SIGTERM); log("sigterm", target="buyer kit group", pid=p)
        except ProcessLookupError:
            pass


def main(dry=False):
    log("start", dry=dry, warn=WARN_AT, stop=STOP_AT, min_gap=MIN_GAP, end=kst(END_AT))
    warned = False
    while True:
        if time.time() >= END_AT: log("end", reason="11-17 18:00 도달"); return
        if not dry and not pids("supervisor") and not pids("kit"):
            log("end", reason="buyer_ops7과 키트가 모두 종료됨"); return
        try:
            s, below, deals = board()
            act, why = decide(s, below)
            log("tick", saved=s, below=below, gap=round(s - below, 4) if below is not None else None, deals=deals, act=act)
            if act == "stop":
                if dry: log("dry_stop", reason=why); return
                stop_buyer(why); return
            if act == "warn" and not warned: log("WARN", reason=why); warned = True
            if act == "ok": warned = False
        except Exception as e:
            log("error", err=repr(e)[:200])
        if dry: return
        time.sleep(120)


if __name__ == "__main__":
    main(dry="--dry" in sys.argv)
