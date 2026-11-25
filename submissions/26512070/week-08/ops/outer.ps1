# The outer runtime: collection, forum, the 2-hour slices and the audit (HARNESS.md 2.2, 2.3).
# Task Scheduler item 2 runs this at log-on; outer.py loops until stopped. It never trades.
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops\outer.ps1
$ErrorActionPreference = "Stop"
$week = Split-Path -Parent $PSScriptRoot
Set-Location $week
$env:PYTHONUTF8 = "1"
$env:MARKET_DIR = "$week\.market"
if (-not $env:OPENROUTER_API_KEY) {
  $env:OPENROUTER_API_KEY = [Environment]::GetEnvironmentVariable('OPENROUTER_API_KEY','User')
}
New-Item -ItemType Directory -Force "$week\logs" | Out-Null
& py "$week\outer.py" *>> "$week\logs\outer-console.log"
