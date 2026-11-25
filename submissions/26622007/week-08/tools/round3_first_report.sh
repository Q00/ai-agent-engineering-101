#!/usr/bin/env bash
# Round 3's first hour in one page: .market/round3_first.md (wait_round3.sh starts this once Round 3 opens).
# Written when the first [supply] line shows, again when the first settle or payment shows, and once more at the end.
#
#   nohup bash tools/round3_first_report.sh > /dev/null 2>&1 &
#   ROUND3_REPORT_MINUTES (default 60), ROUND3_REPORT_DIR (the week-08 folder, for tests)
set -u
HERE="${ROUND3_REPORT_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
LOG="$HERE/logs/market-kit.log"
OUT="$HERE/.market/round3_first.md"
END=$(( $(date +%s) + ${ROUND3_REPORT_MINUTES:-60} * 60 ))

write() {   # $1: why this version was written
  {
    echo "# Round 3 첫 기록 (nocha12)"
    echo
    echo "- 작성: $(date '+%Y-%m-%d %H:%M:%S') ($1)"
    echo "- 시작 표시: $(cat "$HERE/.market/round3_started" 2>/dev/null || echo 없음)"
    /usr/bin/python3 -I - "$HERE/.market/inventory.json" "$HERE/.market/opening_state.json" <<'PY'
import json, sys
def load(p):
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
inv, op = load(sys.argv[1]), load(sys.argv[2])
units = inv.get("units") or []
print(f"- 잔고: {inv.get('balance')} (시작 {inv.get('start')}), 재고 {len(units)}개, 원가 합 {round(sum(u.get('cost') or 0 for u in units))}")
print(f"- 공급 창: {inv.get('restock')}")
if op:
    print(f"- 개장 단계: {sorted(op.get('sent', {}))} 보냄, 탐색 최악원가 누적 {op.get('spent_worst')}")
    for n in (op.get("notes") or [])[-5:]:
        print(f"  - {n}")
PY
    echo
    echo "## SETUP 6줄"
    echo '```'
    /bin/bash "$HERE/tools/round3_setup_check.sh" 2>&1 | sed 's/^/  /' || true
    echo '```'
    for pat in "\[restock\] (opening|supply window|phase)" "\[supply\]" "\[acp\] settled" "\[acp\] paid" "\[acp\] not paying|checkout refused|settle refused" "\[loss\]" "\[forum\]"; do
      echo
      n="$(grep -E -c -- "$pat" "$LOG" 2>/dev/null)"
      echo "## $(echo "$pat" | cut -c1-40) (처음 12줄, 전체 ${n:-0})"
      echo '```'
      grep -E -m12 -- "$pat" "$LOG" 2>/dev/null | cut -c1-220
      echo '```'
    done
    echo
    n="$(grep -c Traceback "$LOG" 2>/dev/null)"
    echo "- Traceback: ${n:-0}건"
  } > "$OUT.tmp" && mv "$OUT.tmp" "$OUT"
}

supply="" money=""
while [ "$(date +%s)" -lt "$END" ]; do
  if [ -z "$supply" ] && grep -q "\[supply\] [0-9]" "$LOG" 2>/dev/null; then supply=1; write "첫 [supply]"; fi
  if [ -z "$money" ] && grep -E -q "\[acp\] (settled|paid)" "$LOG" 2>/dev/null; then money=1; write "첫 정산/결제"; fi
  sleep 30
done
write "${ROUND3_REPORT_MINUTES:-60}분 경과"
