#!/usr/bin/env bash
# Lab STEP 5 in one go. Needs seller_agent.py running on :9999 and the a2a CLI on PATH.
#   bash lab_cli.sh 2>&1 | tee logs/lab-cli.txt
A=http://127.0.0.1:9999
run() { echo; echo "\$ $*"; "$@"; echo "exit code $?"; }
tid() { python -c "import sys,json; print(json.load(sys.stdin)['id'])"; }

echo "### 1. card"
run a2a card get $A

echo; echo "### 2. SendMessage without the A2A-Version header"
echo "\$ curl -s $A/ -H 'Content-Type: application/json' -d '{...SendMessage...}'"
curl -s $A/ -H 'Content-Type: application/json' -d '{
  "jsonrpc": "2.0", "id": 1, "method": "SendMessage",
  "params": {"message": {"role": "ROLE_USER", "messageId": "m0",
                         "parts": [{"text": "I want to buy a lamp"}]}}}'
echo

echo; echo "### 3. buy a lamp: 80, then 105"
echo "\$ a2a send -a $A -o json \"I want to buy a lamp\"   (task id taken from the JSON)"
T=$(a2a send -a $A -o json "I want to buy a lamp" | tee logs/lab-lamp-open.json | tid)
echo "task id: $T"
run a2a send -a $A --task-id "$T" 80
run a2a send -a $A --task-id "$T" 105
run a2a task get -a $A "$T" -o json

echo; echo "### 4. one more message to the finished task"
run a2a send -a $A --task-id "$T" 110 -o json

echo; echo "### 5. the other terminal states"
run a2a send -a $A "I want to buy a sofa"
echo "\$ a2a send -a $A -o json \"I want to buy a chair\""
C=$(a2a send -a $A -o json "I want to buy a chair" | tid); echo "task id: $C"
run a2a send -a $A --task-id "$C" 100
run a2a send -a $A --task-id "$C" 100
run a2a send -a $A --task-id "$C" 100
echo "\$ a2a send -a $A -o json \"I want to buy a bookshelf\""
B=$(a2a send -a $A -o json "I want to buy a bookshelf" | tid); echo "task id: $B"
run a2a task cancel -a $A "$B"
