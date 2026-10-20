#!/usr/bin/env bash
# Drive the seller with the official A2A CLI (a2a 0.3.0).
# Usage: bash lab/lab_cli.sh [http://127.0.0.1:9999]   (set A2A=/path/to/a2a if it is not on PATH)
URL=${1:-http://127.0.0.1:9999}
A2A=${A2A:-a2a}
tid() { awk '/^Task:/{print $2; exit}'; }

echo '$ a2a card get' "$URL"
"$A2A" card get "$URL"

echo
echo '$ curl (SendMessage without the A2A-Version header)'
curl -s "$URL/" -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"ROLE_USER","messageId":"m0","parts":[{"text":"I want to buy a lamp"}]}}}'
echo

echo
echo '$ a2a send -a' "$URL" '"I want to buy a lamp"'
OUT=$("$A2A" send -a "$URL" "I want to buy a lamp"); echo "$OUT"; T=$(echo "$OUT" | tid)

echo
echo '$ a2a send -a' "$URL" '--task-id <task> 80'
"$A2A" send -a "$URL" --task-id "$T" 80

echo
echo '$ a2a send -a' "$URL" '--task-id <task> 105'
"$A2A" send -a "$URL" --task-id "$T" 105

echo
echo '$ a2a task get -a' "$URL" '<task>'
"$A2A" task get -a "$URL" "$T"

echo
echo '$ a2a send -a' "$URL" '--task-id <task> 110 -o json'
"$A2A" send -a "$URL" --task-id "$T" 110 -o json; echo "exit code $?"

echo
echo '$ a2a send -a' "$URL" '"I want to buy a sofa"'
"$A2A" send -a "$URL" "I want to buy a sofa"

echo
echo '$ a2a send -a' "$URL" '"I want to buy a chair", then 100 three times'
OUT=$("$A2A" send -a "$URL" "I want to buy a chair"); T2=$(echo "$OUT" | tid)
for i in 1 2 3; do "$A2A" send -a "$URL" --task-id "$T2" 100 | sed -n '3,4p'; done

echo
echo '$ a2a send -a' "$URL" '"I want to buy a bookshelf", then a2a task cancel'
OUT=$("$A2A" send -a "$URL" "I want to buy a bookshelf"); T3=$(echo "$OUT" | tid)
"$A2A" task cancel -a "$URL" "$T3" | sed -n '1,4p'
