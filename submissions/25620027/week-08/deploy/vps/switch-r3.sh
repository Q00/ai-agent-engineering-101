#!/usr/bin/env bash
# One-shot: switch the running release to the Round 3 settings and relaunch.
# Scheduled by cron for 2026-11-03 00:05 KST (= 2026-11-02 15:05 UTC); removes its
# own cron line afterwards. Changes only two private settings: the manager's model
# switch and the stop time. Keys, market identity and the kit are untouched.
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
BASE=/docker/ax-week08-25620027
RELEASE="${1:-a6a64bb10282}"
LOG="$BASE/switch-r3.log"
exec >>"$LOG" 2>&1
echo "== $(date -u +%FT%TZ) switching to Round 3 settings (release $RELEASE)"
sed -i 's/^AX_R3_MANAGER_MODEL=.*/AX_R3_MANAGER_MODEL=1/' "$BASE/private/model-market.env"
grep -q '^AX_R3_MANAGER_MODEL=1$' "$BASE/private/model-market.env"
python3 - "$BASE/private/runtime.json" <<'EOF'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    settings = json.load(f)
settings["stop_at"] = "2026-11-17T23:59:59+09:00"
settings["remaining_orders"] = 0
with open(path, "w", encoding="utf-8") as f:
    json.dump(settings, f, indent=1)
    f.write("\n")
print("runtime.json:", {k: settings[k] for k in ("public_url", "port", "remaining_orders", "stop_at")})
EOF
chown 10001:10001 "$BASE/private/runtime.json"
chmod 600 "$BASE/private/runtime.json"
"$BASE/launch-r3.sh" "$RELEASE"
touch "$BASE/r3-active"
# the health check now watches the Round 3 run; this one-shot line is no longer needed
crontab -l 2>/dev/null | grep -v 'switch-r3.sh' | crontab -
sleep 90
echo "-- kit log tail after relaunch"
tail -n 8 "$BASE/data/r3/logs/market-kit.log" || true
echo "== $(date -u +%FT%TZ) switch done"
