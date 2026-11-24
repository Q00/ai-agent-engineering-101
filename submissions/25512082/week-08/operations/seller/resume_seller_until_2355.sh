#!/bin/sh
# Round 3 재개 (사용자가 명시적으로 재개 명령을 보냈을 때만 실행). 11-17 20:15 계획된 일시 중단 이후용.
# 조건: Seller 기존 재고 판매만, 신규 매입·Buyer 재시작·SKU/가격 변경 금지, 안전 STOP 조건 유지, 23:55 KST 전체 종료, 23:55 이후 재개 금지.
# 확인 하나라도 실패하면 아무것도 띄우지 않고 종료한다.
O=/home/user/.local/state/ax-round3-ops
A=/home/user/student/AX/ai-agent-engineering-101-week08-v4/submissions/25512082/week-08
L=$O/resume_20261117.log
say() { echo "$(TZ=Asia/Seoul date '+%F %T') $*" | tee -a "$L"; }

NOW=$(date +%s); CUT=$(TZ=Asia/Seoul date -d '2026-11-17 23:55:00' +%s)
[ "$NOW" -ge "$CUT" ] && { say "ABORT: 23:55 KST passed - no resume, results only"; exit 1; }
if ps -eo args | grep -E '^[^ ]*(python|sh)[^ ]* .*(market_kit\.py|run_market_locked|seller\.py|sell_guard|finalize_2026|ops1[0-9]\.py|price_exp|buyer_ops)|^cloudflared' | grep -v grep; then
  say "ABORT: trading-related process already running (no duplicate start)"; exit 1; fi
for f in restock-approval.json restock-approval.claimed.json; do [ -e "$A/$f" ] && { say "ABORT: $f exists (new purchase forbidden)"; exit 1; }; done

# 꺼져 있는 동안의 원장 변화와 재고 확인 (HANDOFF-20261117-2015.json 의 cursor 이후)
python3 -I - "$O/HANDOFF-20261117-2015.json" "$A/.market/inventory.json" <<'EOF' | tee -a "$L"
import json, sys, time, urllib.request
h = json.load(open(sys.argv[1])); inv = json.load(open(sys.argv[2]))
units = sorted(u["unit"] for u in inv.get("units", []))
if units != ["u-5a9bafc674c7"]:
    print(f"ABORT: inventory units changed {units}"); sys.exit(1)
cur = h["ledger"]["cursor_last_seq"]; keys = h["ledger"]["match_keys"]; found = []
while True:
    e = json.load(urllib.request.urlopen(f"https://market.ourolabs.ai/api/ledger?after={cur}", timeout=60))
    e = e if isinstance(e, list) else e.get("entries", [])
    if not e: break
    found += [x for x in e if any(k in json.dumps(x, ensure_ascii=False) for k in keys)]
    cur = e[-1]["seq"]
if found:
    print("ABORT: our ledger entries appeared while paused:", json.dumps([(x["seq"], x["kind"]) for x in found])); sys.exit(1)
print(f"OK: no ledger entries for our shop/unit since {h['ledger']['cursor_last_seq']} (checked to seq {cur}); inventory unchanged")
EOF
[ $? -eq 0 ] || { say "ABORT: pre-resume check failed (see above)"; exit 1; }

cd "$A" || exit 1
export MARKET_OWNER="$(cat /home/user/.config/ax-week07/market-owner)" OWN_AGENT_NAME=sohyunkim11 PATH="/home/user/.local/bin:$PATH"
echo "=== resume $(TZ=Asia/Seoul date '+%F %T') shop-only until 23:55 (after planned pause 20:15), no restock approval ===" >> .market/shop-only-console.out
setsid nohup ./run_market_locked.sh --tunnel --shop-only >> .market/shop-only-console.out 2>&1 < /dev/null &
sleep 2
SUP=$(pgrep -f '^/bin/sh ./run_market_locked.sh --tunnel --shop-only$' | head -1)
[ -n "$SUP" ] || { say "ERROR: seller supervisor not found after start"; exit 1; }
say "seller started: run_market_locked.sh pid $SUP"
cd "$O" && setsid nohup python3 -I sell_guard_2355.py "$SUP" >> sell_guard_2355.stdout 2>&1 < /dev/null &
sleep 2; say "guard: $(pgrep -fx "python3 -I sell_guard_2355.py $SUP" || echo 'NOT RUNNING')"

FIN=$(TZ=Asia/Seoul date -d '2026-11-17 23:58:30' +%s)
if [ "$(date +%s)" -lt "$FIN" ] && [ ! -e "$O/FINAL-20261117/FINAL_RESULT.json" ] && ! pgrep -fx 'python3 -I finalize_20261117.py' >/dev/null; then
  cd "$O" && setsid nohup python3 -I finalize_20261117.py >> FINAL-20261117/finalize.stdout 2>&1 < /dev/null &
  sleep 2; say "finalizer: $(pgrep -fx 'python3 -I finalize_20261117.py' || echo 'NOT RUNNING')"
else
  say "finalizer not started (past 23:58:30, result exists, or already running)"
fi
