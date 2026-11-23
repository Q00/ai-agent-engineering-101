"""A·B등급 시장 중앙가 호가 실험 제어 (2026-11-16 승인, 최대 12시간, 11-17 18:00을 넘지 않음).

- 5분마다 최근 3시간 시장 정산에서 승인 SKU 10종의 A·B등급 중앙가를 계산해
  .market/price-experiment.json 에 원자적으로 기록한다(표본 3건 미만 등급은 기록하지 않음 → 기존 호가).
- 자동 원복(파일 삭제 후 종료):
  · 실험 중 판매 10건 이상에서 평균 실현 이익 < 800
  · 실험 중 결제 리뷰 5건 이상에서 평균 별점 < 4.3
  · 6시간 경과 시 시간당 정산 < 1.82 (기존 2.6건/시간의 70%)
  · ops15가 안전 중단·종료되면 즉시 원복
- 12시간 또는 11-17 18:00 도달 시 파일 삭제 후 종료. LLM 호출 없음.
"""
import json, os, statistics, time, urllib.request, datetime as dt
from pathlib import Path

HERE = Path(__file__).parent
W = Path("/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08")
FILE = W / ".market" / "price-experiment.json"
OPS = HERE / "ops15_state.json"
LOG = HERE / "price_exp.log"
KST = dt.timezone(dt.timedelta(hours=9))
SKUS = {"G2560a8c9", "G2108d9bc", "G1831f2c0", "G0a5f780c", "G38519afd", "G7ced7d9f", "G0d52d508", "G772009ce", "Ga03a71a7", "G2d6e8e91"}
FINAL_END = dt.datetime(2026, 11, 17, 18, 0, tzinfo=KST).timestamp()
MIN_PROFIT, MIN_STARS, MIN_RATE = 800, 4.3, 1.82


def kst(t=None): return dt.datetime.fromtimestamp(t or time.time(), KST).strftime("%m-%d %H:%M:%S")
def log(kind, **kw):
    kw.update(t=kst(), kind=kind)
    with LOG.open("a") as f: f.write(json.dumps(kw, ensure_ascii=False) + "\n")
def get(a):
    r = json.load(urllib.request.urlopen(f"https://market.ourolabs.ai/api/ledger?after={a}", timeout=30))
    return r if isinstance(r, list) else r.get("entries", [])


def remove(reason):
    try: FILE.unlink()
    except FileNotFoundError: pass
    log("revert_or_end", reason=reason)


def main():
    start = time.time(); until = min(start + 12 * 3600, FINAL_END)
    top = get(-1)[-1]["seq"]; lo, hi = top - 400000, top
    while hi - lo > 500:
        m = (lo + hi) // 2; e = get(m)
        if e and e[0]["ts"] < start - 3 * 3600: lo = m
        else: hi = m
    cur, settles = lo, []
    log("start", until=kst(until))
    while True:
        now = time.time()
        if now >= until: remove("12시간 또는 최종 종료 시각 도달"); return
        try:
            for _ in range(200):
                e = get(cur)
                if not e: break
                for x in e:
                    d = x.get("data") or {}
                    if x["kind"] == "settle" and d.get("sku") in SKUS and d.get("grade") in ("A", "B"):
                        settles.append((x["ts"], d["sku"], d["grade"], d["amount"]))
                cur = e[-1]["seq"]
                if len(e) < 500: break
            settles = [s for s in settles if s[0] >= now - 3 * 3600]
            asks = {}
            for sku in SKUS:
                for g in ("A", "B"):
                    v = [a for t, s, gg, a in settles if s == sku and gg == g]
                    if len(v) >= 3: asks.setdefault(sku, {})[g] = int(statistics.median(v))
            st = json.loads(OPS.read_text()) if OPS.exists() else None
            if st and (st.get("stopped") or st.get("final")):
                remove(f"ops15 종료/중단: {st.get('stopped')}"); return
            if st:
                units = {u: p for w in st["windows"] for p in w.get("purchases", []) for u in p["units"]}
                exp = [s for s in st["settles"] if s["unit"] in units and s["ts"] >= start]
                prof = [s["amount"] - units[s["unit"]]["unit_cost"] for s in exp]
                revs = [r["stars"] for r in st["results"] if r.get("verified") and r["ts"] >= start and r.get("stars")]
                if len(prof) >= 10 and statistics.mean(prof) < MIN_PROFIT:
                    remove(f"평균 실현 이익 {statistics.mean(prof):.0f} < {MIN_PROFIT}"); return
                if len(revs) >= 5 and statistics.mean(revs) < MIN_STARS:
                    remove(f"결제 리뷰 평균 {statistics.mean(revs):.2f} < {MIN_STARS}"); return
                if now - start >= 6 * 3600 and len(exp) / ((now - start) / 3600) < MIN_RATE:
                    remove(f"시간당 정산 {len(exp) / ((now - start) / 3600):.2f} < {MIN_RATE}"); return
                log("tick", asks=len(asks), exp_sales=len(prof), avg_profit=round(statistics.mean(prof)) if prof else None,
                    reviews=len(revs), avg_stars=round(statistics.mean(revs), 2) if revs else None)
            tmp = FILE.with_suffix(".tmp")
            tmp.write_text(json.dumps({"until": until, "written": time.time(), "asks": asks}, ensure_ascii=False))
            os.replace(tmp, FILE)
        except Exception as ex:
            log("error", err=repr(ex)[:200])   # 갱신 실패가 30분 넘으면 Seller는 자동으로 기존 호가 사용
        time.sleep(300)


if __name__ == "__main__":
    main()
