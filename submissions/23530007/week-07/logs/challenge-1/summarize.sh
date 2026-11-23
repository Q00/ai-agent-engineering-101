#!/usr/bin/env bash
# Challenge 1: my shop's deals and average prices per kit run, from the archived Round 2 kit log.
# A run starts at each "[kit] market_kit" line. The launch-pricing change (commit 357bdb6) was live from the
# restart at 10-20 12:44:59 (the last run). Usage: logs/challenge-1/summarize.sh > logs/challenge-1/summary.txt
zcat "$(dirname "$0")/../archive/market-kit-v1-until-20261030-0744.log.gz" | awk '
/\[kit\] market_kit/ {seg++; start[seg]=$1; n[seg]=0}
{last[seg]=$1}
/\[order [^]]*\].*round [0-9]/ {match($0,/round ([0-9]+)/,m); rnd[seg]=m[1]}
/\[seller\].*memory: [a-z]+ deal at/ {match($0,/memory: ([a-z]+) deal at ([0-9]+)/,m); n[seg]++; it[seg","m[1]]++; pr[seg","m[1]]+=m[2]}
END {for (s=1; s<=seg; s++) if (n[s]) printf "run %2d %s..%s round %s: my shop sold %d | lamp %d avg %.1f, chair %d avg %.1f, bookshelf %d avg %.1f\n",
  s, start[s], last[s], rnd[s], n[s], it[s",lamp"], (it[s",lamp"] ? pr[s",lamp"]/it[s",lamp"] : 0),
  it[s",chair"], (it[s",chair"] ? pr[s",chair"]/it[s",chair"] : 0), it[s",bookshelf"], (it[s",bookshelf"] ? pr[s",bookshelf"]/it[s",bookshelf"] : 0)}'
