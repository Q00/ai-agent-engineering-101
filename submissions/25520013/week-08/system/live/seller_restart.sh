#!/usr/bin/env bash
# Restart the seller only, at a quiet moment, so rule code the kernel just changed is the code that runs.
# Called by kernel/code_edit.py (A2_SELLER_RESTART_CMD) with the change id as the last argument, cwd = data root.
# Contract: writes a2/seller_restart_result.json {"id", "ok", "ts", "why"} and exits 0 on success, non-zero otherwise.
# It never touches files of the rule code: on failure the kernel restores its backup.
# Same gate as the human deploys (deploy_v2120.sh): no established connection to the seller port, no open buyer task,
# seller idle 5 s, three polls in a row; SIGTERM to the seller's process group only (the kit respawns the seller).
set -u
ID="${1:-0}"
W="$(pwd)"
OUT="$W/a2/seller_restart_result.json"
GATE_S="${A2_RESTART_GATE_S:-1200}"
res() { # ok why
  python3 - "$OUT" "$ID" "$1" "$2" <<'PY'
import json, os, sys, time
out, cid, ok, why = sys.argv[1:5]
try:
    cid = int(cid)
except ValueError:
    pass
tmp = out + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    json.dump({"id": cid, "ok": ok == "1", "ts": time.time(), "why": why[:300]}, f, ensure_ascii=False)
os.replace(tmp, out)
PY
  echo "RESULT ok=$1 $2"
}
kcmd() { tr "\0" " " < "/proc/$1/cmdline" 2>/dev/null; }
ts() { date +%Y%m%dT%H%M%S; }

SLINE=$(pgrep -af "python3 hooks/seller.py --port [0-9]+" | head -1)
PORT=$(printf "%s" "$SLINE" | grep -o -- "--port [0-9]*" | grep -o "[0-9]*" | head -1)
[ -n "$PORT" ] || { res 0 "seller process with --port not found"; exit 2; }
lpid() { ss -ltnpH "sport = :$PORT" 2>/dev/null | grep -o "pid=[0-9]*" | head -1 | cut -d= -f2; }
estc() {
  local o
  o=$(ss -tnH state established "( sport = :$PORT )" 2>/dev/null) || { echo ERR; return; }
  [ -n "$(ss -ltnH "sport = :$PORT" 2>/dev/null)" ] || { echo ERR; return; }
  if [ -z "$o" ]; then echo 0; else printf "%s\n" "$o" | wc -l | tr -d " "; fi
}
OPN=$(cat <<'PY'
import json, time
now = time.time(); last, done = {}, set()
with open("logs/buyer_attempts.jsonl", "rb") as f:
    f.seek(0, 2); f.seek(max(0, f.tell() - 3_000_000)); f.readline()
    for ln in f:
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        t, tid = r.get("ts"), r.get("task")
        if not isinstance(t, (int, float)) or not tid or t < now - 900:
            continue
        last[tid] = t
        if r.get("event") == "outcome":
            done.add(tid)
print(sum(1 for k, t in last.items() if k not in done and now - t < 125))
PY
)
opn() { python3 -c "$OPN" < /dev/null 2>/dev/null || echo ERR; }
idle() {
  local lt
  lt=$(tail -n 3000 .r3/metrics.jsonl 2>/dev/null | grep -E '"hook": *"seller"' \
    | grep -E '"decision": *"(open|accept|reject|no_stock|declined)"' | tail -1 \
    | grep -o '"ts": *[0-9]*' | grep -o '[0-9]*' | head -1)
  if [ -n "$lt" ]; then echo $(( $(date +%s) - lt )); else echo ERR; fi
}

# 1. the code on disk must import and the policy must validate BEFORE the running seller is stopped
uv run --no-sync python - <<'PYT' || { res 0 "import smoke failed: the new code does not load"; exit 3; }
import json, sys
sys.path[:0] = [".", "hooks"]
import policy as P, seller as S  # noqa: F401
P.validate(json.load(open("policy.json")))
assert hasattr(S, "Seller")
print("SMOKE ok")
PYT

# 2. who is the seller, and is its group separate from the kit
SP=$(lpid); SPG=$(ps -o pgid= -p "${SP:-0}" | tr -d " ")
[ -n "$SP" ] && [ -n "$SPG" ] || { res 0 "no listener on the seller port"; exit 4; }
case "$(kcmd "$SPG")" in "/bin/sh -c uv run --no-sync python hooks/seller.py --port $PORT"*) ;; *) res 0 "group leader is not the seller shell"; exit 4 ;; esac
KIT=$(ps -o ppid= -p "$SPG" | tr -d " "); KCMD="$(kcmd "$KIT")"; KPG=$(ps -o pgid= -p "$KIT" | tr -d " ")
case "$KCMD" in *"market_kit.py join"*) ;; *) res 0 "parent of the seller is not the kit"; exit 4 ;; esac
[ "$SPG" != "$KPG" ] || { res 0 "seller group equals kit group"; exit 4; }
[ "$(ps -o pid= -g "$SPG" | wc -l)" -le 4 ] || { res 0 "unexpected members in the seller group"; exit 4; }
echo "INFO seller=$SP group=$SPG kit=$KIT kitpgid=$KPG port=$PORT $(ts)"

# 3. quiet gate
T0=$(date +%s); Z=0; i=0
while :; do
  i=$((i + 1)); E=$(estc); O=$(opn); I=$(idle)
  if [ "$E" = ERR ] || [ "$O" = ERR ] || [ "$I" = ERR ]; then res 0 "gate check error est=$E open=$O idle=$I"; exit 5; fi
  if [ "$E" = 0 ] && [ "$O" = 0 ] && [ "$I" -ge 5 ]; then Z=$((Z + 1)); else Z=0; fi
  if [ "$Z" -ge 3 ]; then E=$(estc); O=$(opn); [ "$E" = 0 ] && [ "$O" = 0 ] && break; Z=0; fi
  [ $(( $(date +%s) - T0 )) -ge "$GATE_S" ] && { res 0 "no quiet moment in ${GATE_S}s (est=$E open=$O idle=$I)"; exit 5; }
  sleep 2
done
[ "$(lpid)" = "$SP" ] || { res 0 "seller pid changed while waiting"; exit 5; }
echo "ACT SIGTERM -- -$SPG (seller group only) after polls=$i $(ts)"
kill -TERM -- "-$SPG"

# 4. the kit must bring up a new seller that answers
n=""
for _ in $(seq 1 36); do sleep 5; n=$(lpid); [ -n "$n" ] && [ "$n" != "$SP" ] && break; done
n=$(lpid)
card=$(timeout 5 curl -s "http://127.0.0.1:$PORT/.well-known/agent-card.json" < /dev/null 2>/dev/null | grep -c '"name"')
if [ -n "$n" ] && [ "$n" != "$SP" ] && [ "$(kcmd "$KIT")" = "$KCMD" ] && [ "${card:-0}" -ge 1 ]; then
  res 1 "seller restarted pid=$n"; exit 0
fi
res 0 "new seller not verified within 3 min (pid=${n:-none} card=${card:-0})"; exit 6
