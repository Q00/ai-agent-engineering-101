# The new layers' runner (design/ideal-harness.md 20-5 step 3): it replaces ops\outer.ps1 after the move.
# Collection, forum, health and emergency, rules watch, class view, participants, reports, the meta-harness cycle.
# It never trades. Never run it together with outer.py (both collect and post).
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops\harness.ps1
$ErrorActionPreference = "Stop"
$week = Split-Path -Parent $PSScriptRoot
Set-Location $week
$env:PYTHONUTF8 = "1"
$env:MARKET_DIR = "$week\.market"
foreach ($name in 'OPENROUTER_API_KEY', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHAT_ID') {
  if (-not [Environment]::GetEnvironmentVariable($name, 'Process')) {
    [Environment]::SetEnvironmentVariable($name, [Environment]::GetEnvironmentVariable($name, 'User'), 'Process')
  }
}
New-Item -ItemType Directory -Force "$week\logs" | Out-Null
& py -m harness.run *>> "$week\logs\harness-console.log"
