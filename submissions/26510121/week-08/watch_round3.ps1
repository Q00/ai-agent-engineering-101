param([switch]$Stop, [switch]$Status)
$ErrorActionPreference = 'Stop'
$agentRoot = $PSScriptRoot
$statePath = Join-Path $agentRoot '.market/round3-watch.json'
if ($Status) {
    if (Test-Path -LiteralPath $statePath) { Get-Content -LiteralPath $statePath -Encoding UTF8 }
    else { Write-Output 'No watcher state exists.' }
    return
}
if ($Stop) {
    New-Item -ItemType File -Path (Join-Path $agentRoot '.market/watch-stop.request') -Force | Out-Null
    Write-Output 'Stop requested; the watcher will also stop the market process it launched.'
    return
}
$pythonPath = Join-Path $agentRoot '.venv/Scripts/python.exe'
$watchPath = Join-Path $agentRoot 'watch_round3.py'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'The week-08 environment is missing.' }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$stdout = Join-Path $agentRoot "logs/watch-launch-$stamp.txt"
$stderr = Join-Path $agentRoot "logs/watch-launch-$stamp.err"
$process = Start-Process -FilePath $pythonPath -ArgumentList @('-u', ('"' + $watchPath + '"')) `
    -WorkingDirectory $agentRoot -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $stdout -RedirectStandardError $stderr
Start-Sleep -Seconds 2
$process.Refresh()
if ($process.HasExited) { throw "Watcher exited; see $stderr" }
Write-Output "Round 3 watcher started (PID $($process.Id)); interval 600 seconds."
Write-Output "Status: $statePath"
