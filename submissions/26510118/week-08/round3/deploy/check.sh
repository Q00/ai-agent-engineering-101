#!/usr/bin/env bash
# Run on the Azure VM: is everything up, and what happened last.
M=/home/azureuser/market/round3
systemctl --no-pager --lines=0 status kiro-kit.service kiro-analysis.service | grep -E "●|Active:"
echo "--- kit log (first line and the last 15)"
head -n 1 "$M/logs/market-kit.log" 2>/dev/null
tail -n 15 "$M/logs/market-kit.log" 2>/dev/null
echo "--- analysis log (last 8)"
tail -n 8 "$M/logs/analysis.log" 2>/dev/null
echo "--- counts"
for k in sales unpaid orders unmet refusals; do
  printf "%-9s %s\n" "$k" "$( { wc -l < "$M/.market/records/$k.jsonl"; } 2>/dev/null || echo 0)"
done
grep -c "\[acp\] settled" "$M/logs/market-kit.log" 2>/dev/null | sed 's/^/shop settled  /'
grep -c "\[acp\] paid" "$M/logs/market-kit.log" 2>/dev/null | sed 's/^/buyer paid    /'
