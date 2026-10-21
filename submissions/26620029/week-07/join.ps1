# join.ps1 - AX week-07: join market.ourolabs.ai as ddolcom (background). ASCII only.
# Run : powershell -NoProfile -ExecutionPolicy Bypass -File D:\ai-agent-engineering-101\join.ps1
# Log : powershell Get-Content D:\ai-agent-engineering-101\.market\join.log -Tail 10 -Wait
# Mode: $Deployed set -> shop on Cloudflare Workers; empty -> local seller via $Mode
# Stop: powershell "taskkill /T /F /PID (Get-Content D:\ai-agent-engineering-101\.market\kit.pid)"

$ErrorActionPreference = "Stop"

# ---- settings ----
$Root   = "D:\ai-agent-engineering-101"
$Py     = Join-Path $Root ".venv\Scripts\python.exe"
$Kit    = Join-Path $Root "scripts\market_kit.py"
$Agent  = Join-Path $Root "submissions\26620029\week-07"
$Market = "https://market.ourolabs.ai"
$Nick   = "ddolcom"
$Mode   = "--tunnel"        # if the tunnel is blocked, change to "--buyer-only"
$Deployed = ""              # Cloudflare Worker URL, e.g. "https://ddolcom-shop.ddolcom.workers.dev" -> registers that shop

# logs/pid go to .market (already in .gitignore)
$OutDir = Join-Path $Root ".market"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Log    = Join-Path $OutDir "join.log"
$ErrLog = Join-Path $OutDir "join.err.log"
$PidF   = Join-Path $OutDir "kit.pid"

# ---- environment ----
$env:PYTHONUTF8   = "1"
$env:MARKET_OWNER = "mk-yHvSre_-Qp640L49"      # claim code -> @ddolcom on the board
# make sure the child process can find cloudflared and claude
$env:Path = "C:\Program Files (x86)\cloudflared;C:\Program Files\cloudflared;$env:USERPROFILE\.local\bin;$env:Path"

# ---- pre-checks ----
foreach ($f in @($Py, $Kit, (Join-Path $Agent "agent.json"))) {
    if (-not (Test-Path $f)) { Write-Host "MISSING: $f"; exit 1 }
}
if ($Deployed) { $ModeArgs = @("--deployed", $Deployed) } else { $ModeArgs = @($Mode) }
if (-not $Deployed -and $Mode -eq "--tunnel" -and -not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared not found. Run: winget install --id Cloudflare.cloudflared"; exit 1
}
$probe = cmd /c "echo Reply with only the number 42| claude -p --model haiku 2>&1" | Out-String
if ($LASTEXITCODE -ne 0) {
    Write-Host "claude harness failed: $($probe.Trim())"
    Write-Host "Fix: run 'claude', type /login, finish in browser, then /exit. Then run this script again."
    exit 1
}
Write-Host "harness ok: $($probe.Trim())"

if (Test-Path $PidF) {
    $old = Get-Content $PidF -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Host "already running (PID $old). Stop first: taskkill /T /F /PID $old"; exit 1
    }
}

# ---- start in background ----
$ArgList = @("-u", "`"$Kit`"", "join", "--dir", "`"$Agent`"", "--market", $Market, "--name", $Nick) + $ModeArgs
Write-Host ("kit args: " + ($ArgList -join " "))
$p = Start-Process -FilePath $Py `
    -ArgumentList $ArgList `
    -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $Log -RedirectStandardError $ErrLog

$p.Id | Out-File $PidF -Encoding ascii
Write-Host "kit started (PID $($p.Id)). Watch: Get-Content $Log -Tail 10 -Wait"
