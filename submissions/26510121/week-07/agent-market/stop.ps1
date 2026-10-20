param([switch]$ShopOnly)
$ErrorActionPreference = 'Stop'
$agentDir = Split-Path -Parent $PSScriptRoot
$statePath = Join-Path $agentDir $(if ($ShopOnly) { '.market/shop-session.json' } else { '.market/session.json' })
if (-not (Test-Path -LiteralPath $statePath)) {
    Write-Output 'No market session has been recorded.'
    return
}
$session = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$process = Get-CimInstance Win32_Process -Filter "ProcessId = $($session.pid)"
if (-not $process) {
    Write-Output 'The recorded market kit is no longer running.'
    return
}
if ($process.Name -ne 'python.exe' -or $process.CommandLine -notmatch '(market_kit|run-kit)\.py') {
    throw 'The recorded PID belongs to a different process; refusing to stop it.'
}
& taskkill /T /F /PID $session.pid
if ($LASTEXITCODE -ne 0) { throw 'Could not stop the market process tree.' }
