param([switch]$Publish)
$ErrorActionPreference = 'Stop'
$round3Python = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $round3Python)) {
    $round3Python = Join-Path $env:LOCALAPPDATA 'Python/bin/python3.14.exe'
}
if (-not $Publish) {
    & $round3Python -X utf8 (Join-Path $PSScriptRoot 'launch_round3.py')
    exit $LASTEXITCODE
}
$round3PidFile = Join-Path $PSScriptRoot '.market/operator.pid'
if (Test-Path -LiteralPath $round3PidFile) {
    $round3Existing = Get-Process -Id ([int](Get-Content -LiteralPath $round3PidFile)) -ErrorAction SilentlyContinue
    if ($round3Existing) { throw 'Saved operator PID is running; inspect it before starting another.' }
}
$round3Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$round3Process = Start-Process -FilePath $round3Python -ArgumentList @('-X','utf8','-u','launch_round3.py','--publish') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $PSScriptRoot "logs/operator-$round3Stamp.out.log") -RedirectStandardError (Join-Path $PSScriptRoot "logs/operator-$round3Stamp.err.log")
Write-Output "Round 3 operator started: PID $($round3Process.Id). Check logs/market-kit.log for registration."
