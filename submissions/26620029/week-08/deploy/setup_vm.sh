#!/bin/bash
# Prepare a fresh Ubuntu 24.04 VM: Docker, 2 GB swap, directories, watchdog and deadline timers. Starts no kit. Safe to run twice.
set -euo pipefail
sudo apt-get update -y
sudo apt-get install -y docker.io docker-compose-v2 git jq
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"

if ! swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  grep -q /swapfile /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

sudo mkdir -p /srv/market/{repo,state/market_dir,secrets,control,backup}
sudo chown -R "$USER":"$USER" /srv/market
chmod 700 /srv/market/secrets
touch /srv/market/control/STOPPED      # nothing may trade until the laptop's kit is stopped
printf 'KIT_UID=%s\nKIT_GID=%s\n' "$(id -u)" "$(id -g)" > /srv/market/control/compose.env

sudo tee /etc/systemd/system/market-watchdog.service >/dev/null <<EOF
[Unit]
Description=Restart the market kit if it is unhealthy
[Service]
Type=oneshot
User=$USER
SupplementaryGroups=docker
ExecStart=/bin/bash /srv/market/repo/submissions/26620029/week-08/deploy/market.sh watchdog
EOF
sudo tee /etc/systemd/system/market-watchdog.timer >/dev/null <<EOF
[Unit]
Description=Market kit watchdog
[Timer]
OnBootSec=5min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF
sudo tee /etc/systemd/system/market-stop.service >/dev/null <<EOF
[Unit]
Description=Stop the market kit when Round 3 ends
[Service]
Type=oneshot
User=$USER
SupplementaryGroups=docker
ExecStart=/bin/bash /srv/market/repo/submissions/26620029/week-08/deploy/market.sh deadline-stop
EOF
sudo tee /etc/systemd/system/market-stop.timer >/dev/null <<EOF
[Unit]
Description=End of Round 3: 2026-11-17 23:59 KST, stop at 00:05
[Timer]
OnCalendar=2026-11-18 00:05:00 Asia/Seoul
Persistent=true
[Install]
WantedBy=timers.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now market-watchdog.timer market-stop.timer
echo "setup done; log out and in for the docker group"
