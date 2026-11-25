# The inner runtime: kit v4 with our seller, market_buyer, restock and review (HARNESS.md 2.2, appendix C).
# Task Scheduler item 1 runs this at log-on; it runs until the kit stops.
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops\inner.ps1
# The claim code (mk-...) is never in the repository: set MARKET_OWNER, or put it in %USERPROFILE%\.market_owner.
param(
  [string]$Market = "https://market.ourolabs.ai",
  [string]$Name = "gom31-certified-goods",
  [string]$CloudflaredDir = "$HOME\ax\agent-market-handoff\bin",
  [switch]$NewKeys
)
$ErrorActionPreference = "Stop"
$week = Split-Path -Parent $PSScriptRoot                  # submissions\26512070\week-08
$repo = (Resolve-Path "$week\..\..\..").Path
Set-Location $week
$env:PYTHONUTF8 = "1"
if (-not $env:MARKET_OWNER) {
  $f = Join-Path $HOME ".market_owner"
  if (Test-Path $f) { $env:MARKET_OWNER = (Get-Content $f -Raw).Trim() }
}
if (-not $env:MARKET_OWNER) { Write-Warning "no claim code: the board will not show the GitHub account" }
if (-not ((Test-Path "$week\shop_key.pem") -and (Test-Path "$week\buyer_key.pem")) -and -not $NewKeys) {
  throw "shop_key.pem / buyer_key.pem are not in week-08 (appendix C step 2). Copy them, or pass -NewKeys to let the kit make new ones."
}
$env:Path = "$CloudflaredDir;" + [Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [Environment]::GetEnvironmentVariable('Path','User')
New-Item -ItemType Directory -Force "$week\logs" | Out-Null
& py "$repo\scripts\market_kit.py" join --dir $week --market $Market --name $Name --tunnel *>> "$week\logs\kit-console.log"
