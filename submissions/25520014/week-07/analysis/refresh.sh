#!/bin/bash
# Refresh the challenge logs and report materials from the public ledger and the lab server.
#   cd submissions/25520014/week-07 && bash analysis/refresh.sh
# Writes logs/challenge-1/, logs/challenge-2/ and materials/; never touches the kit logs themselves.
set -e
cd "$(dirname "$0")/.."
PY=../../../.venv/bin/python
SRV='~/agent-market/submissions/25520014/week-07'
mkdir -p logs/challenge-1 logs/challenge-2 materials/deploy materials/server

echo "[refresh] ledger summaries"
$PY analysis/c1_compare.py > logs/challenge-1/before-after.txt
$PY analysis/c2_dishonest.py > logs/challenge-2/ledger-summary.txt
$PY analysis/rounds.py > materials/rounds.txt

echo "[refresh] desk decisions and dishonest-shop excerpts from the server kit log"
ssh jungmin "L=$SRV/logs/market-kit.log
echo '# Challenge 1: every pricing-desk decision on the lab server (grep -n \"[desk]\" in the server kit log, times UTC, lines unchanged)'
grep -n '\[desk\]' \$L" > logs/challenge-1/desk-decisions.txt
ssh jungmin "L=$SRV/logs/market-kit.log
conv() { a=\$(grep -n \"\$1\" \$L | head -1 | cut -d: -f1); b=\$(awk -v s=\$a 'NR>s && /\[market\] ledger #/{print NR; exit}' \$L); awk -v a=\$a -v b=\$b 'NR>=a && NR<=b && !/\[seller\]/ {print NR\": \"\$0}' \$L; }
echo '# Challenge 2 excerpts from the lab server kit log (~/agent-market/.../logs/market-kit.log, Round 2, times UTC)'
echo '# Extracted with grep/awk; lines unchanged, the source line number is the prefix; our own [seller] lines left out.'
echo; echo '## Every time the buyer chose bright-living or maple-home in Round 2 (the reason is on the line)'
grep -n '\[choose\].*\(bright-living\|maple-home\)' \$L
echo; echo '## 10-22 04:36 UTC: bright-living, the MARKET NOTICE in every message; the harness only sees the last number (5)'
conv '04:36:19 \[buyer\] \[choose\].*bright-living'
echo; echo '## 10-21 21:10 UTC: maple-home says Deal: lamp at 104, the receipt says 119; the kit does not sign, our buyer gives 1 star'
conv '21:10:33 \[buyer\] \[choose\].*maple-home'
echo; echo '## Every receipt the kit refused to sign in Round 2'
grep -n '\[sign\]' \$L | grep -v 'sig_seller ok, sig_buyer added'" > logs/challenge-2/server-kit-log-excerpts.txt

echo "[refresh] server copies: kit log, buyer memory, watchdog"
scp -q "jungmin:$SRV/logs/market-kit.log" materials/server/market-kit.log
scp -q "jungmin:$SRV/memory.jsonl" materials/server/memory.jsonl
scp -q jungmin:agent-market/watchdog.log materials/deploy/server-watchdog.log
ssh jungmin "grep -o '\[choose\] [^ ]* ([^)]*): [a-z]*' $SRV/logs/market-kit.log | awk '{print \$NF}' | sort | uniq -c" \
  > materials/server/choose-reasons.txt
wc -l logs/challenge-1/* logs/challenge-2/* materials/rounds.txt materials/server/*
