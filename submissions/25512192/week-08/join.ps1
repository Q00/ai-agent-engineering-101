# Joins the market with kit v4 in the background. The claim code is never stored here:
#   $env:MARKET_OWNER = "mk-..."; powershell -NoProfile -ExecutionPolicy Bypass -File submissions\25512192\week-08\join.ps1
# Leave: taskkill /T /F /PID (Get-Content submissions\25512192\week-08\kit.pid)
$dir = $PSScriptRoot
$repo = Resolve-Path "$dir\..\..\.."
if (-not $env:MARKET_OWNER) { Write-Error "set `$env:MARKET_OWNER to the claim code from https://market.ourolabs.ai/join"; exit 1 }
$env:PYTHONUTF8 = "1"
$p = Start-Process -FilePath python -WindowStyle Hidden -PassThru -WorkingDirectory $repo `
  -ArgumentList "scripts\market_kit.py", "join", "--dir", "$dir", "--market", "https://market.ourolabs.ai", "--name", "winun1127", "--tunnel" `
  -RedirectStandardOutput "$dir\join.out" -RedirectStandardError "$dir\join.err"
$p.Id | Set-Content "$dir\kit.pid"
"kit started, pid $($p.Id); log: $dir\logs\market-kit.log"
