#!/usr/bin/env bash
# Install the server side of the local-agent loop (idempotent):
#   hub   ~/git/ai-agent-engineering-101.git  bare repo local agents clone from and push to (ssh)
#   hook  ops/hub_pre_receive.py copied into the hub (a copy: a pushed change to it takes effect only on reinstall)
#   timer week08-deploy.timer runs ops/deploy_pull.py every 2 min
set -euo pipefail
W8="$(cd "$(dirname "$0")/.." && pwd)"
REPO="$(cd "$W8/../../.." && pwd)"
HUB="$HOME/git/ai-agent-engineering-101.git"
[ -d "$HUB" ] || git init -q --bare "$HUB"
git -C "$HUB" config receive.denyNonFastForwards true     # no force push: history is grading evidence
git -C "$HUB" config receive.denyDeletes true
git -C "$HUB" config uploadpack.allowFilter true          # lets a local clone skip big old logs (--filter=blob:limit=20m)
git -C "$HUB" config uploadpack.allowAnySHA1InWant true
git -C "$HUB" symbolic-ref HEAD refs/heads/week-08
git -C "$REPO" remote get-url hub >/dev/null 2>&1 || git -C "$REPO" remote add hub "$HUB"
install -m 755 "$W8/ops/hub_pre_receive.py" "$HUB/hooks/pre-receive"
U="$HOME/.config/systemd/user"; mkdir -p "$U"
cat > "$U/week08-deploy.service" <<UNIT
[Unit]
Description=Week 08: deploy week-08 from the server hub (ops/deploy_pull.py)
[Service]
Type=oneshot
WorkingDirectory=$W8
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/usr/bin/python3 $W8/ops/deploy_pull.py
TimeoutStartSec=45min
UNIT
cat > "$U/week08-deploy.timer" <<UNIT
[Unit]
Description=Week 08: check the hub for new commits every 2 min
[Timer]
OnBootSec=2min
OnUnitInactiveSec=2min
[Install]
WantedBy=timers.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now week08-deploy.timer
systemctl --user list-timers week08-deploy.timer --no-pager
