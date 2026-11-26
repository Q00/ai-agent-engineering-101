#!/usr/bin/env bash
# Run on the Azure VM after the files, the keys and the two env files are in place (see DEPLOY.md).
# Checks what must be there, installs the two services and starts them.
set -euo pipefail
M=/home/azureuser/market
for f in "$M/market_kit.py" "$M/round3/agent.json" "$M/round3/shop_key.pem" "$M/round3/buyer_key.pem" \
         "$M/openai.env" "$M/owner.env" "$M/.venv/bin/python"; do
  [ -e "$f" ] || { echo "missing: $f"; exit 1; }
done
chmod 600 "$M/round3/"*.pem "$M/openai.env" "$M/owner.env"
"$M/.venv/bin/python" -c "import a2a, httpx, uvicorn, cryptography, mcp" || { echo "the venv lacks a package (mcp: pip install mcp==1.30.0)"; exit 1; }
command -v cloudflared >/dev/null || { echo "cloudflared is not installed"; exit 1; }
sudo cp "$M/round3/deploy/kiro-kit.service" "$M/round3/deploy/kiro-analysis.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now kiro-kit.service kiro-analysis.service
sleep 5
systemctl --no-pager --lines=0 status kiro-kit.service kiro-analysis.service || true
echo "installed; follow the kit with: tail -f $M/round3/logs/market-kit.log"
