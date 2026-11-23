"""Substitute option A, 6 h experiment (person's decision 11/08 ~13:30): turns it on at START (subst_exp.json; the
seller code is in since the 14:06 restart), checks the stop rules every CHECK_S from the ledger, ends it at END
(mode off) and sends the report.
  stop -> mode B ("not in stock" at once, no alternatives) + urgent Telegram, when arm A has >= MIN_N receipts and
    paid share of A receipts < 30%, or
    mismatch reviews on A deals >= 5 and >= 10% of A results, or
    A deals' average stars <= normal deals' average - 0.3; from 11/09 ~12:00 paid deals only (the board's basis),
    once >= MIN_N paid A results carry stars; unpaid stars are recorded, not judged
  paid = the ledger result of the receipt's task says settled; stars are kept apart for paid and unpaid results
  (the board's stars count paid deals only, agent.md; results carry stars either way)
Every A / control result is written to logs/subst_exp_results.jsonl.
    python tools/subst_watch.py            (log in logs/subst_watch.log)
"""
import json
import os
import re
import sys
import time
from datetime import datetime

import httpx

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.chdir(HERE)
START = datetime(2026, 11, 8, 15, 0).timestamp()
END = START + 6 * 3600
CHECK_S = 1800
MIN_N = 20
MARKET = "https://market.ourolabs.ai"
LOG = os.path.join(HERE, "logs", "subst_watch.log")
EXP = os.path.join(HERE, "logs", "subst_exp.jsonl")
RES = os.path.join(HERE, "logs", "subst_exp_results.jsonl")
MISMATCH = re.compile(r"not match|mismatch|did not match|wrong item|different item|not what i (asked|ordered)|불일치", re.I)
ME_DISPLAY = "Choo Shop"


def log(m):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%m-%d %H:%M:%S} {m}\n")


def set_mode(**kw):
    from depts import subst_exp
    r = subst_exp.rules()
    r.update(kw)
    with open(subst_exp.RULES, "w", encoding="utf-8") as f:
        json.dump(r, f)


class Ledger:
    """Results of my receipts (by receipt task) from the ledger, read on from where the last call stopped."""
    def __init__(self, after):
        self.after, self.res = after, {}
        self.c = httpx.Client(timeout=60)

    def pull(self):
        while True:
            page = None
            for _ in range(5):
                try:
                    page = self.c.get(f"{MARKET}/api/ledger", params={"after": self.after}).json()
                    break
                except Exception:
                    time.sleep(5)
            if not page:
                return
            for e in page:
                d = e.get("data") or {}
                rc = d.get("receipt") or {}
                if e.get("kind") == "result" and rc.get("shop") == ME_DISPLAY and rc.get("task"):
                    self.res[rc["task"]] = {"ts": e["ts"], "buyer": d.get("buyer"), "stars": d.get("stars"),
                                            "review": d.get("review") or "",
                                            "settled": bool((d.get("checks") or {}).get("settled")),
                                            "price": rc.get("price"), "item": rc.get("item")}
            self.after = page[-1]["seq"]
            if len(page) < 500:
                return


def exp_tasks(t0):
    out = {}
    try:
        with open(EXP, encoding="utf-8") as f:
            for ln in f:
                e = json.loads(ln)
                if e["ts"] >= t0 and e.get("arm") in ("A", "control"):
                    out.setdefault(e["task"], {"arm": e["arm"], "events": set()})["events"].add(e["event"])
    except OSError:
        pass
    return out


def metrics(L, t0):
    tasks = exp_tasks(t0)
    m = {}
    for arm in ("A", "control"):
        ts = {t: v for t, v in tasks.items() if v["arm"] == arm}
        rec = [t for t, v in ts.items() if "receipt" in v["events"]]
        res = [L.res[t] for t in rec if t in L.res]
        paid = [r for r in res if r["settled"]]
        unpaid = [r for r in res if not r["settled"]]
        avg = lambda rs: round(sum(r["stars"] for r in rs) / len(rs), 2) if rs else None
        star = lambda rs: [r for r in rs if r["stars"] is not None]
        m[arm] = {"offers": sum(1 for v in ts.values() if "offer" in v["events"]),
                  "no_offer": sum(1 for v in ts.values() if "no_offer" in v["events"]),
                  "number_replies": sum(1 for v in ts.values() if "number_reply" in v["events"]),
                  "receipts": len(rec), "results": len(res), "paid": len(paid), "unpaid": len(unpaid),
                  "no_result": len(rec) - len(res),
                  "stars_paid": avg(star(paid)), "stars_paid_n": len(star(paid)),
                  "stars_unpaid": avg(star(unpaid)), "stars_unpaid_n": len(star(unpaid)),
                  "stars_all": avg(star(res)), "stars_all_n": len(star(res)),
                  "mismatch": sum(1 for r in res if MISMATCH.search(r["review"])),
                  "revenue": sum(int(r["price"] or 0) for r in paid)}
    exp = set(tasks)
    normal = [r for t, r in L.res.items() if t not in exp and r["ts"] >= t0 and r["stars"] is not None]
    m["normal"] = {"results": len(normal), "stars_all": round(sum(r["stars"] for r in normal) / len(normal), 2) if normal else None,
                   "stars_paid": (lambda p: round(sum(r["stars"] for r in p) / len(p), 2) if p else None)(
                       [r for r in normal if r["settled"]])}
    return m


def stop_reason(m):
    a, n = m["A"], m["normal"]
    if a["receipts"] < MIN_N:
        return None
    if a["results"] and a["paid"] / max(1, a["receipts"]) < 0.30:
        return f"대체(A) 영수증 결제율 {a['paid']}/{a['receipts']} < 30%"
    if a["mismatch"] >= 5 and a["mismatch"] >= 0.10 * max(1, a["results"]):
        return f"대체(A) 품목 불일치 리뷰 {a['mismatch']}건 / 결과 {a['results']}건"
    # 11/09 ~12:00 (person): stars of paid deals only, as on the board, from MIN_N paid A results with stars; the
    # unpaid ones are kept in the numbers but not judged (11:58 stop: all 4.14 vs 4.44, paid only 4.71 vs 4.46)
    if a["stars_paid_n"] >= MIN_N and n["stars_paid"] is not None and a["stars_paid"] <= n["stars_paid"] - 0.3:
        return f"대체(A) 결제 거래 별점 {a['stars_paid']} <= 일반 결제 거래 {n['stars_paid']} - 0.3 (A 결제 {a['stars_paid_n']}건)"
    return None


def text(m, t0, t1, stopped=None):
    a, c, n = m["A"], m["control"], m["normal"]
    pr = lambda x: f"{x['paid'] / x['receipts']:.0%}" if x["receipts"] else "-"
    ur = lambda x: f"{(x['unpaid'] + x['no_result']) / x['receipts']:.0%}" if x["receipts"] else "-"
    return "\n".join([
        f"대체 상품 A안 실험 ({datetime.fromtimestamp(t0):%H:%M} ~ {datetime.fromtimestamp(t1):%H:%M})"
        + (f" - 중단(B로 전환): {stopped}" if stopped else ""),
        f"- A: 대체 제안 {a['offers']} (후보 없어 바로 재고 없음 {a['no_offer']}), 숫자 응답 {a['number_replies']}, 성사(영수증) "
        f"{a['receipts']}, 결제 {a['paid']} ({pr(a)}), 미결제 {a['unpaid'] + a['no_result']} ({ur(a)}; 결과 없음 {a['no_result']})",
        f"   별점 결제 있음 {a['stars_paid']} (n {a['stars_paid_n']}) / 없음 {a['stars_unpaid']} (n {a['stars_unpaid_n']}), "
        f"품목 불일치 리뷰 {a['mismatch']}, 추가 매출(결제된 A) {a['revenue']:,}",
        f"- 대조(기존 방식): 대체 제안 {c['offers']}, 성사 {c['receipts']}, 결제 {c['paid']}, 매출 {c['revenue']:,}",
        f"- 일반 거래 별점: 전체 {n['stars_all']} / 결제된 것 {n['stars_paid']} (결과 {n['results']})"])


def main():
    log(f"waiting for {datetime.fromtimestamp(START):%H:%M}")
    while time.time() < START - 120:
        time.sleep(30)
    c = httpx.Client(timeout=60)
    top = c.get(f"{MARKET}/api/ledger", params={"after": -1}).json()[-1]["seq"]
    L = Ledger(top)
    set_mode(mode="A_exp", share=0.5, since=round(START), until=round(END))
    import alerts
    import decisions
    decisions.record("sales", "subst_exp_on", "subst_exp", "A_exp",
                     "person 11/08 ~13:30: option A on 50% of substitute offers, 6 h; candidates same category +-30%; "
                     "number reply = offer for the first; receipt item = category name")
    alerts.send("info", "subst_exp_on", f"대체 상품 A안 실험 시작 {datetime.fromtimestamp(START):%H:%M} ~ "
                f"{datetime.fromtimestamp(END):%H:%M} (대체 제안의 50%). 30분마다 중단 기준 확인", log=log)
    stopped = None
    while time.time() < END:
        time.sleep(min(CHECK_S, max(5, END - time.time())))
        L.pull()
        m = metrics(L, START)
        log(json.dumps(m, ensure_ascii=False))
        why = stop_reason(m)
        if why and not stopped:
            stopped = why
            set_mode(mode="B", stopped=round(time.time()), stop_reason=why)
            decisions.record("sales", "subst_exp_stop", "subst_exp", "B", why)
            alerts.send("urgent", "subst_exp_stop", "대체 상품 A안 중단 -> B(바로 재고 없음): " + why + "\n"
                        + text(m, START, time.time(), stopped), log=log)
            break
    time.sleep(1200)                                  # results come 2-9 s after a deal; payments within a minute
    L.pull()
    m = metrics(L, START)
    if not stopped:
        set_mode(mode="off", ended=round(time.time()))
        decisions.record("sales", "subst_exp_end", "subst_exp", "off", "6 h done: back to the old substitute offers")
    with open(RES, "w", encoding="utf-8") as f:
        for t, r in L.res.items():
            f.write(json.dumps(dict(task=t, **r), ensure_ascii=False) + "\n")
    alerts.send("daily", f"subst_exp_report:{int(START)}", text(m, START, min(END, time.time()), stopped)
                + ("" if stopped else "\n6시간 끝: 기존 방식으로 돌아감(mode off). 계속할지는 사람 결정"), log=log)
    log("report sent")


if __name__ == "__main__":
    main()
