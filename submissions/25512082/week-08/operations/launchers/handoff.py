"""18:10 종료 직전 인수인계 기록 생성 (읽기 전용 수집 → 새 파일 2개만 작성, 기존 파일 수정 없음)."""
import json, re, subprocess, sys, time, urllib.request, datetime as dt
from pathlib import Path
K = dt.timezone(dt.timedelta(hours=9)); kst = lambda t: dt.datetime.fromtimestamp(t, K).strftime("%m-%d %H:%M:%S")
A = Path("/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08"); M = A / ".market"
O = Path("/home/user/.local/state/ax-round3-ops"); N = Path("/home/user/.local/state/ax-round3-next"); B = Path("/home/user/.local/state/ax-round3-buyer2")
out = Path(sys.argv[1]) if len(sys.argv) > 1 else O / "HANDOFF-20261117-1810"
H = {"written_at": kst(time.time())}
log = (A / "logs/market-kit.log").read_text(errors="ignore").splitlines()
st = [l for l in log if "[acp] settled" in l]
H["last_settle_line"] = st[-1] if st else None
m = re.search(r"ledger #(\d+), unit (u-[0-9a-f]+)", st[-1]) if st else None
H["last_settle_ledger"], H["last_settle_unit"] = (int(m.group(1)), m.group(2)) if m else (None, None)
H["kit_log_lines"], H["kit_log_last_line"] = len(log), log[-1] if log else None
def run(f):
    p = O / f
    if not p.exists(): return None
    s = json.loads(p.read_text())
    return {"spent": s.get("spent"), "units_bought": s.get("units_bought"), "stopped": s.get("stopped"), "final": bool(s.get("final")),
            "end": kst(s["end"]) if s.get("end") else None, "windows": len(s.get("windows", [])), "ledger_cursor": s.get("cursor")}
H["ops15"], H["ops16"] = run("ops15_state.json"), run("ops16_state.json")
spent = sum((x or {}).get("spent") or 0 for x in (H["ops15"], H["ops16"])); units = sum((x or {}).get("units_bought") or 0 for x in (H["ops15"], H["ops16"]))
H["approved_total"] = {"cap": 900000, "spent": spent, "remaining": 900000 - spent, "unit_cap": 100, "units": units, "units_remaining": 100 - units}
inv = json.loads((M / "inventory.json").read_text())
H["wallet_balance"] = inv.get("balance")
H["unsold_units"] = [{"unit": u.get("unit"), "sku": u.get("sku"), "name": (u.get("name") or "")[:40], "grade": u.get("grade"), "cost": u.get("cost"),
                      "supplied": kst(u["supplied_at"]) if u.get("supplied_at") else None, "expires": kst(u["expires_at"]) if u.get("expires_at") else None}
                     for u in inv.get("units", [])]
H["pending_approval_files"] = [n for n in ("restock-approval.json", "restock-approval.claimed.json") if (A / n).exists()]
ev = [json.loads(l) for l in (M / "seller-events.jsonl").read_text().splitlines()[-200:] if l.strip()]
acc = [e for e in ev if e.get("event") == "accept" and time.time() - e["ts"] < 900]
settled_units = {re.search(r"unit (u-[0-9a-f]+)", l).group(1) for l in st[-50:] if re.search(r"unit (u-[0-9a-f]+)", l)}
H["recent_accepts_without_settle_15min"] = [{"ts": kst(e["ts"]), "unit": e.get("unit"), "price": e.get("price")} for e in acc if e.get("unit") not in settled_units]
try:
    b = json.load(urllib.request.urlopen("https://market.ourolabs.ai/api/board?round=3", timeout=30))
    s = next(y for y in b["shops"] if y["name"] == "sohyunkim11"); x = next(y for y in b["buyers"] if y["name"] == "sohyunkim11")
    stu = sorted([p for p in b["players"] if p.get("student")], key=lambda p: -p["student_total"]); i = [p["player"] for p in stu].index("sohyunkim11")
    H["board"] = {"measured_at": kst(time.time()), "shop": {k: s.get(k) for k in ("deals", "revenue", "book", "stars", "reviews", "stars_rank", "refunds", "broken", "stock_cost")},
                  "buyer": {k: x.get(k) for k in ("orders", "deals", "saved", "forum", "posts")},
                  "student_shop": stu[i]["student_shop"], "student_buyer": stu[i]["student_buyer"], "student_total": stu[i]["student_total"], "student_rank": i + 1,
                  "rank_32": stu[31]["student_total"], "rank_34": stu[33]["student_total"]}
except Exception as e:
    H["board"] = {"error": repr(e)[:200]}
ps = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout.splitlines()
H["trading_processes_remaining"] = [l.strip()[:150] for l in ps if re.search(r"market_kit.py|run_market_locked|seller.py|cloudflared|ops1[56].py|price_exp|buyer_ops|saved_guard|stop_guard|launch_ops16", l) and "grep" not in l and "bash -c" not in l and "handoff" not in l]
H["stop_records"] = {"ops15": [l[:200] for l in (O / "ops15.log").read_text().splitlines() if re.search(r'"kind": "(STOP|end|stopped_final)"', l)][-2:],
                     "ops16": [l[:200] for l in (O / "ops16.log").read_text().splitlines() if re.search(r'"kind": "(STOP|end|stopped_final)"', l)][-2:] if (O / "ops16.log").exists() else [],
                     "buyer": [l[:200] for l in (B / "saved_guard7.log").read_text().splitlines() if '"STOP"' in l][-1:],
                     "price_exp": [l[:200] for l in (O / "price_exp.log").read_text().splitlines() if "revert_or_end" in l][-1:]}
H["shutdown_log"] = (N / "shutdown_1810.log").read_text().splitlines() if (N / "shutdown_1810.log").exists() else []
out.with_suffix(".json").write_text(json.dumps(H, ensure_ascii=False, indent=1))
bd = H["board"]
md = f"""# Round 3 인수인계 기록 ({H['written_at']} KST)

## 상태 요약
- 마지막 정상 정산: `{H['last_settle_line']}` (ledger #{H['last_settle_ledger']}, unit {H['last_settle_unit']})
- 누적 매입(승인 총량 900,000원·100개): ops15 {(H['ops15'] or {}).get('spent')}원 / {(H['ops15'] or {}).get('units_bought')}개, ops16 {(H['ops16'] or {}).get('spent') if H['ops16'] else '실행 안 됨'}원 / {(H['ops16'] or {}).get('units_bought') if H['ops16'] else '-'}개 → 합계 {spent}원 / {units}개, 남은 한도 {900000 - spent}원 / {100 - units}개
- 시장 지갑 잔액: {H['wallet_balance']}
- 미판매 재고: {json.dumps(H['unsold_units'], ensure_ascii=False)}
- 남은 승인 파일: {H['pending_approval_files'] or '없음'}
- 최근 15분 합의 후 미정산: {json.dumps(H['recent_accepts_without_settle_15min'], ensure_ascii=False) or '없음'}
- board: {json.dumps(bd, ensure_ascii=False)}
- 남은 거래 프로세스: {H['trading_processes_remaining'] or '없음'}
- 안전 STOP/종료 기록: {json.dumps(H['stop_records'], ensure_ascii=False)}

## 적용 중이던 설정·안전 조건 (변경 없음)
- Seller: SKU 10종·단가 상한, 판매율 70%·이익 중앙값 1,000·원가 미만 15%·3h 정산 20건·surge 최악원가, 동시 보유 150,000, 손실/환불/파손/만료/중복정산/stars_rank<3.93/저평가 계정 2개/2h 정산 없음 STOP
- 신규 매입 승인 종료 시각: 19:00 KST (ops16). 19:00 이후 재개 시 신규 매입 없이 기존 재고 판매만.
- Buyer: 11-17 03:23 Saved 감시자 기준으로 중단, 재시작 금지.
- 가격 실험: 11-17 05:08 정상 종료(파일 삭제).

## 재시작 시 확인할 것
1. 자동 시작 항목 없음(crontab 없음, systemd 사용자 타이머는 launchpadlib만, autostart 없음). 재부팅만으로는 거래가 시작되지 않는다.
2. ops15·ops16 상태 파일이 있으므로 같은 감독은 다시 시작되지 않는다(재사용 거부). 신규 매입은 새 승인 없이는 하지 않는다.
3. Seller 판매만 재개할 때: `cd {A} && MARKET_OWNER=... ./run_market_locked.sh --tunnel --shop-only` (owner 값은 보호 파일에서 읽음). 키트가 새 터널로 가게를 다시 등록한다.
4. 재개 전 `.market/inventory.json`의 재고와 위 마지막 정산 ledger 번호 이후의 settle을 원장에서 확인해 중복 정산이 없는지 본다.
5. 20:00 KST에 모든 거래를 마무리하고 PR 작업으로 넘어간다(Week-08 PR 마감 11-23 23:59).
"""
out.with_suffix(".md").write_text(md)
print(out.with_suffix(".md"))
