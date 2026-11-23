# Reload the market seller with the current seller.py without restarting the kit (the tunnel URL stays).
#   powershell -File reload_seller.ps1 -Port <seller port from the kit log>
# With SELLER_RELOAD=1 the uvicorn reloader owns the port and runs the seller in a worker process.
# 1. Touch seller.py so the reloader has a change to act on. Stopping the worker without one left the
#    reloader waiting and the shop down for two minutes (personal PC, 2026-10-21).
# 2. If the old worker is still there (on the laptop the watcher got stuck waiting for it to exit),
#    stop it: the reloader then starts a fresh worker from the current seller.py on the same port.
# 3. Check that the card answers.
param([Parameter(Mandatory = $true)][int]$Port)
$seller = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "seller.py"
$reloader = (Get-NetTCPConnection -State Listen -LocalPort $Port).OwningProcess | Select-Object -First 1
$old = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$reloader").ProcessId

(Get-Item $seller).LastWriteTime = Get-Date
Start-Sleep -Seconds 8
$now = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$reloader").ProcessId
if ($old -and $now -and -not (Compare-Object $old $now)) {
    $old | ForEach-Object { Stop-Process -Id $_ -Force; "stopped stuck worker $_" }
    Start-Sleep -Seconds 10
}
Get-CimInstance Win32_Process -Filter "ParentProcessId=$reloader" | ForEach-Object { "worker $($_.ProcessId)" }
try {
    $code = (Invoke-WebRequest -UseBasicParsing -TimeoutSec 15 "http://127.0.0.1:$Port/.well-known/agent-card.json").StatusCode
    "card $code"
} catch { "CARD NOT ANSWERING: touch seller.py again or restart the scheduled task" }
