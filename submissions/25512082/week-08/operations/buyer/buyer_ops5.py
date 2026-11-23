"""Buyer 개선 운영 감독 스크립트 #2 (2026-11-15 10:1x 승인: 굿즈 A등급만 결제, 최대 3,000주문, 11-16 10:00 KST 종료).
- 첫 100주문에도 관문 기준(정산률 70%, 거래당 Saved 0.75, 파손 2%)을 적용한다.

- 기존 buyer 코드/설정을 그대로 쓰는 별도 폴더의 buyer-only Kit을 100주문 단위(--orders 100)로 실행한다.
- 매 100주문: 공식 board로 구간 정산 수와 구간 거래당 Saved를 역산해 기록한다.
  구간 Saved 합 = saved_after*(deals_after+100) - saved_before*(deals_before+100)
- 매 500주문(관문): 최근 500주문 정산률 >= 70%, 구간 거래당 Saved >= 0.75,
  파손/환불 비율 <= 2%, 인증/결제/중복 문제 없음 -> 다음 500주문 진행. 아니면 신규 주문 중단.
- 추가 안전: 100주문 구간 Saved < 0.65 이거나 파손/환불 비율 > 2% 이면 즉시 중단.
- board가 이번 구간 결제를 반영하지 못해 검증할 수 없으면 중단(확대하지 않음).
- 3,000주문 또는 11-16 10:00 KST 도달 시 종료. 자동 연장 없음. LLM 호출 없음.
"""
import json, os, signal, subprocess, sys, time, urllib.request, datetime as dt
from pathlib import Path

HERE = Path(__file__).parent
AGENT = HERE / "agent"
REPO = Path("/home/user/student/AX/ai-agent-engineering-101-week08-v4")
LOG, STATE = HERE / "buyer_ops5.log", HERE / "buyer_ops5_state.json"
KST = dt.timezone(dt.timedelta(hours=9))
END_AT = dt.datetime(2026, 11, 16, 17, 0, tzinfo=KST).timestamp()  # 다음 구간 11-16 10:00~17:00
MAX_ORDERS, BLOCK, GATE = 4000, 100, 500  # 다음 구간 승인: 최대 4,000주문
MIN_RATE, MIN_SAVED, MAX_BAD, HARD_MIN_SAVED = 0.65, 0.75, 0.02, 0.65  # 2026-11-15 11:1x 승인: 정산률 기준 65%


def kst(t=None): return dt.datetime.fromtimestamp(t or time.time(), KST).strftime("%m-%d %H:%M:%S")
def log(kind, **kw):
    kw.update(t=kst(), kind=kind)
    with LOG.open("a") as f: f.write(json.dumps(kw, ensure_ascii=False) + "\n")


def board_buyer():
    b = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30))
    x = next(y for y in b["buyers"] if y["name"] == "sohyunkim11")
    s = next(y for y in b["shops"] if y["name"] == "sohyunkim11")
    stu = sorted([p for p in b["players"] if p.get("student")], key=lambda p: -p["student_total"])
    i = [p["player"] for p in stu].index("sohyunkim11")
    tot = [p["student_total"] for p in stu]
    return {"deals": x["deals"], "saved": x["saved"], "orders": x.get("orders"), "forum": x.get("forum"),
            "shop_all": stu[i]["student_shop"], "buyer_all": stu[i]["student_buyer"],
            "student_total": stu[i]["student_total"], "rank": i + 1,
            "t40": tot[39], "t35": tot[34], "t30": tot[29], "shop_refunds": s.get("refunds")}


def paid_count():
    p = AGENT / ".market" / "payments.jsonl"
    if not p.exists(): return 0, 0
    rows = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    orders = [r.get("order") for r in rows if r.get("paid")]
    return len(orders), len(orders) - len(set(orders))  # (paid, duplicate paid orders)


LEDGER = {"cursor": None}


def ledger_broken():
    """2026-11-15 17:4x 승인(1안): 원장의 broken 기록을 읽어 우리 buyer 파손을 환불(0점)과 −1(환불 없음)로 구분."""
    def g(a):
        r = json.load(urllib.request.urlopen(f"https://market.ourolabs.ai/api/ledger?after={a}", timeout=30))
        return r if isinstance(r, list) else r.get("entries", [])
    if LEDGER["cursor"] is None:
        LEDGER["cursor"] = g(-1)[-1]["seq"]
        return 0, 0, []
    refunds = minus1 = 0; rows = []
    for _ in range(400):
        e = g(LEDGER["cursor"])
        if not e: break
        for x in e:
            d = x.get("data") or {}
            if x["kind"] == "broken" and d.get("buyer") == "sohyunkim11":
                if d.get("refunded"): refunds += 1
                else: minus1 += 1
                rows.append({"seq": x["seq"], "item": d.get("item"), "refunded": d.get("refunded"), "warranty_hours": d.get("warranty_hours")})
        LEDGER["cursor"] = e[-1]["seq"]
        if len(e) < 500: break
    return refunds, minus1, rows


def broken_count():
    p = AGENT / ".market" / "broken.jsonl"
    return sum(1 for l in p.read_text().splitlines() if l.strip()) if p.exists() else 0


PAY402 = {"run": 0}


def scan_problems(lines, run):
    """2026-11-15 승인: 시장 결제 거절(402)은 결제 성공 없이 연속 3건일 때만 문제로 본다.
    그 밖의 인증·결제 실패는 즉시 문제. 반환: (문제 목록, 갱신된 연속 402 수)."""
    bad = []
    for l in lines:
        if "[acp] paid" in l: run = 0
        elif "payment not completed: 402" in l:
            run += 1
            if run >= 3: bad.append(f"시장 결제 거절 연속 {run}건: {l[:120]}")
        elif "buyer token refused" in l or "join as buyer failed" in l or "payment not completed" in l:
            bad.append(l[:160])
    return bad, run


def kit_problems(since_line):
    lines = (AGENT / "logs" / "market-kit.log").read_text(errors="ignore").splitlines()[since_line:]
    bad, PAY402["run"] = scan_problems(lines, PAY402["run"])
    return len(lines) + since_line, bad


def segment(before, after, n_orders, paid_delta, broken_delta, refunds=None):
    """구간 지표. 순수 함수. 1안: broken_delta에는 −1(환불 없는 파손)만 넣고, 보증 환불은 refunds로 따로 기록."""
    deals = after["deals"] - before["deals"]
    ssum = after["saved"] * (after["deals"] + 100) - before["saved"] * (before["deals"] + 100)
    return {"orders": n_orders, "paid_local": paid_delta, "deals_board": deals,
            "settle_rate": deals / n_orders if n_orders else 0.0,
            "saved_per_deal": ssum / deals if deals > 0 else None,
            "broken": broken_delta, "refunds": refunds, "bad_rate": broken_delta / deals if deals > 0 else 1.0}


def block_ok(seg):
    """100주문 구간 즉시 중단 판단: (계속 여부, 사유)."""
    if seg["deals_board"] != seg["paid_local"]: return False, "board가 구간 결제를 모두 반영하지 않음(검증 불가)"
    if seg["deals_board"] == 0: return False, "구간 정산 0건"
    if seg["saved_per_deal"] is None or seg["saved_per_deal"] < HARD_MIN_SAVED: return False, f"구간 거래당 Saved {seg['saved_per_deal']} < 0.65"
    if seg["bad_rate"] > MAX_BAD: return False, f"−1(환불 없는 파손) 비율 {seg['bad_rate']:.1%} > 2%"
    return True, ""


def gate_ok(seg, problems):
    """500주문 관문: (확대 여부, 사유)."""
    if problems: return False, f"인증/결제 문제 {problems[:2]}"
    if seg["deals_board"] != seg["paid_local"]: return False, "board 반영 불일치(검증 불가)"
    if seg["settle_rate"] < MIN_RATE: return False, f"정산률 {seg['settle_rate']:.0%} < 70%"
    if seg["saved_per_deal"] is None or seg["saved_per_deal"] < MIN_SAVED: return False, f"거래당 Saved {seg['saved_per_deal']} < 0.75"
    if seg["bad_rate"] > MAX_BAD: return False, f"−1(환불 없는 파손) 비율 {seg['bad_rate']:.1%} > 2%"
    return True, ""


def wait_board(expect_deals, timeout=900):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            bb = board_buyer()
            if bb["deals"] >= expect_deals: return bb
        except Exception: pass
        time.sleep(30)
    try: return board_buyer()
    except Exception: return None


def run_block(n):
    env = dict(os.environ, OWN_AGENT_NAME="sohyunkim11",
               MARKET_OWNER=Path("/home/user/.config/ax-week07/market-owner").read_text().strip())
    cmd = [str(REPO / ".venv/bin/python"), str(REPO / "scripts/market_kit.py"), "join", "--dir", str(AGENT),
           "--market", "https://market.ourolabs.ai", "--name", "sohyunkim11", "--buyer-only", "--orders", str(n)]
    p = subprocess.Popen(cmd, env=env, stdout=open(HERE / "kit.stdout", "a"), stderr=subprocess.STDOUT, start_new_session=True)
    while p.poll() is None:
        if time.time() >= END_AT:
            os.killpg(p.pid, signal.SIGTERM); p.wait(timeout=60); return "deadline"
        time.sleep(10)
    return p.returncode


def main():
    if STATE.exists(): raise SystemExit("buyer_ops3 상태 파일이 이미 있음: 중단된 운영은 재사용하지 않음")
    st = {"start": time.time(), "orders": 0, "blocks": [], "gates": [], "stopped": None}
    base = board_buyer(); st["base"] = base; ledger_broken(); log("start", base=base, end=kst(END_AT), rule="1안: 2%는 −1만, 환불은 별도 기록")
    st["refunds_total"] = 0; st["minus1_total"] = 0; st["broken_rows"] = []; gate_minus1 = 0; gate_refunds = 0
    gate_before = base; gate_paid0, _ = paid_count(); gate_broken0 = broken_count(); gate_orders0 = 0
    kit_line = len((AGENT / "logs" / "market-kit.log").read_text(errors="ignore").splitlines())
    gate_problems = []
    while st["orders"] < MAX_ORDERS and time.time() < END_AT and not st["stopped"]:
        before = board_buyer(); paid0, _ = paid_count(); broken0 = broken_count()
        n = min(BLOCK, MAX_ORDERS - st["orders"])
        rc = run_block(n)
        paid1, dup = paid_count(); broken1 = broken_count()
        done = n if rc == 0 else None
        if rc == "deadline":
            st["stopped"] = "종료 시각 도달(정상 종료)"; log("deadline"); break
        st["orders"] += n
        after = wait_board(before["deals"] + (paid1 - paid0))
        if after is None:
            st["stopped"] = "board 조회 불가(검증 불가로 중단)"; log("STOP", reason=st["stopped"]); break
        try:
            rf, m1, rows = ledger_broken()
        except Exception as e:
            st["stopped"] = f"원장 조회 불가(−1 판정 불가로 중단): {e!r}"[:200]; log("STOP", reason=st["stopped"]); break
        st["refunds_total"] += rf; st["minus1_total"] += m1; st["broken_rows"] += rows; gate_minus1 += m1; gate_refunds += rf
        seg = segment(before, after, n, paid1 - paid0, m1, rf)
        kit_line, probs = kit_problems(kit_line); gate_problems += probs
        if dup: probs.append(f"중복 결제 주문 {dup}")
        st["blocks"].append({"orders_total": st["orders"], **seg, "board": after, "problems": probs})
        log("block", orders_total=st["orders"], seg=seg, board=after, problems=probs, rc=rc)
        ok, why = block_ok(seg)
        if ok and st["orders"] == BLOCK:          # 첫 100주문: 확대 기준 적용
            ok, why = gate_ok(seg, probs)
            log("first_gate", ok=ok, why=why, seg=seg)
        if not ok or probs:
            st["stopped"] = why or f"인증/결제 문제 {probs[:2]}"; log("STOP", reason=st["stopped"]); break
        if st["orders"] - gate_orders0 >= GATE:
            gseg = segment(gate_before, after, st["orders"] - gate_orders0, paid1 - gate_paid0, gate_minus1, gate_refunds)
            ok, why = gate_ok(gseg, gate_problems)
            st["gates"].append({"orders_total": st["orders"], **gseg, "ok": ok, "why": why, "board": after})
            log("gate", orders_total=st["orders"], seg=gseg, ok=ok, why=why, board=after)
            if not ok:
                st["stopped"] = f"관문 미통과: {why}"; log("STOP", reason=st["stopped"]); break
            gate_before, gate_paid0, gate_broken0, gate_orders0, gate_problems = after, paid1, broken1, st["orders"], []
            gate_minus1 = gate_refunds = 0
        STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))
    st["final"] = board_buyer() if not st.get("final") else st["final"]
    log("end", orders=st["orders"], stopped=st["stopped"], final=st["final"])
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
