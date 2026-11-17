#!/usr/bin/env bash
# Start the one-way copy of our market data to Supabase (ops/supabase_sync.py) every 2 min. Needs:
#   .market/supabase.env (gitignored, chmod 600) with  SUPABASE_DB_URL=postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres
#   (the direct db.<ref>.supabase.co host is IPv6 only and this server has no IPv6 route; outbound 5432 is blocked
#    here, so the session pooler is unreachable too: use the transaction pooler on 6543. Our project: aws-0-ap-northeast-1)
#   psycopg in ~/.venvs/week08-qa  (pip install "psycopg[binary]")
set -euo pipefail
W8="$(cd "$(dirname "$0")/.." && pwd)"
PY="$HOME/.venvs/week08-qa/bin/python"
ENVF="$W8/.market/supabase.env"
[ -f "$ENVF" ] || { echo "missing $ENVF (SUPABASE_DB_URL=...)"; exit 1; }
chmod 600 "$ENVF"
"$PY" -c "import psycopg" || { echo "psycopg missing in $PY"; exit 1; }
"$PY" "$W8/ops/supabase_sync.py"            # first copy now, in the foreground: fails loudly on a bad URL
U="$HOME/.config/systemd/user"; mkdir -p "$U"
cat > "$U/week08-supabase-sync.service" <<UNIT
[Unit]
Description=Week 08: copy our market data to Supabase (ops/supabase_sync.py)
[Service]
Type=oneshot
WorkingDirectory=$W8
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=$PY $W8/ops/supabase_sync.py
TimeoutStartSec=10min
Nice=10
UNIT
cat > "$U/week08-supabase-sync.timer" <<UNIT
[Unit]
Description=Week 08: Supabase copy every 2 min
[Timer]
OnActiveSec=1min
OnUnitInactiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now week08-supabase-sync.timer
systemctl --user list-timers week08-supabase-sync.timer --no-pager
