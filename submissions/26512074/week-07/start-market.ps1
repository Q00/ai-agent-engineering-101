$ErrorActionPreference = 'Stop'
$week07Root = $PSScriptRoot
$week07Python = Join-Path $week07Root '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $week07Python)) { throw 'Create .venv and install requirements.txt first.' }
$week07PidFile = Join-Path $week07Root '.market/operator.pid'
if (Test-Path -LiteralPath $week07PidFile) {
    $week07Existing = Get-Process -Id ([int](Get-Content -LiteralPath $week07PidFile)) -ErrorAction SilentlyContinue
    if ($week07Existing) { throw 'An operator may already be running. Check .market/operator.pid before starting another.' }
}
$week07Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Start-Process -FilePath $week07Python -ArgumentList @('-X', 'utf8', '-u', 'run_market.py') -WorkingDirectory $week07Root -WindowStyle Hidden -RedirectStandardOutput (Join-Path $week07Root "logs/operator-$week07Stamp.out.log") -RedirectStandardError (Join-Path $week07Root "logs/operator-$week07Stamp.err.log")
Write-Output 'Market operator started. Keep this PC powered on and signed in; see logs/market-kit.log.'
