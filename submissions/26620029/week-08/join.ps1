# join.ps1 - AX week-08: join market.ourolabs.ai as ddolcom (background). ASCII only.
# Run : powershell -NoProfile -ExecutionPolicy Bypass -File D:\ai-agent-engineering-101\submissions\26620029\week-08\join.ps1
# Log : powershell Get-Content D:\ai-agent-engineering-101\.market\join.log -Tail 10 -Wait
# Mode: $Deployed set -> shop on Cloudflare Workers; empty -> local seller via $Mode
# Stop: powershell "taskkill /T /F /PID (Get-Content D:\ai-agent-engineering-101\.market\kit.pid)"
# Keep: the script starts itself again hidden with -Supervise; that copy restarts the kit whenever it exits
#       (e.g. the market could not read the card through a fresh tunnel and refused the join), and logs each
#       start and exit to .market\supervisor.log. kit.pid holds the supervisor, so Stop ends both.
param([switch]$Supervise)

$ErrorActionPreference = "Stop"

# ---- settings ----
$Root   = "D:\ai-agent-engineering-101"
$Py     = Join-Path $Root ".venv\Scripts\python.exe"
$Kit    = Join-Path $Root "scripts\market_kit.py"
$Agent  = Join-Path $Root "submissions\26620029\week-08"
$Market = "https://market.ourolabs.ai"
$Nick   = "ddolcom"
$Mode   = "--tunnel"        # if the tunnel is blocked, change to "--buyer-only"
$Deployed = ""              # week-08: kit shop via $Mode (Round 3 stock/ACP need the kit); Workers was "https://ddolcom-shop.ddolcom.workers.dev"

# logs/pid go to .market (already in .gitignore)
$OutDir = Join-Path $Root ".market"
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
$Log    = Join-Path $OutDir "join.log"
$ErrLog = Join-Path $OutDir "join.err.log"
$PidF   = Join-Path $OutDir "kit.pid"
$SupLog = Join-Path $OutDir "supervisor.log"
$RestartWait, $ShortRun, $ShortMax, $BackoffWait = 30, 300, 3, 900   # seconds; 3 runs under 5 min in a row -> wait 15 min

# ---- environment ----
$env:PYTHONUTF8   = "1"
# claim code -> @ddolcom on the board; kept in market_owner.txt (gitignored), not in this file
$OwnerF = Join-Path $Agent "market_owner.txt"
if (-not (Test-Path $OwnerF)) { Write-Host "MISSING: $OwnerF (one line: your claim code)"; exit 1 }
$env:MARKET_OWNER = (Get-Content $OwnerF -TotalCount 1).Trim()
# make sure the child process can find cloudflared and claude
$env:Path = "C:\Program Files (x86)\cloudflared;C:\Program Files\cloudflared;$env:USERPROFILE\.local\bin;$env:Path"

if ($Deployed) { $ModeArgs = @("--deployed", $Deployed) } else { $ModeArgs = @($Mode) }
$ArgList = @("-u", "`"$Kit`"", "join", "--dir", "`"$Agent`"", "--market", $Market, "--name", $Nick) + $ModeArgs

# ---- supervisor: run the kit, and start it again whenever it exits ----
if ($Supervise) {
    $short = 0
    while ($true) {
        try {
            $t0 = Get-Date
            Add-Content $SupLog "$(Get-Date -Format 'MM-dd HH:mm:ss') kit start"
            $p = Start-Process -FilePath $Py -ArgumentList $ArgList -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput $Log -RedirectStandardError $ErrLog
            $p.WaitForExit()
            $secs = [int]((Get-Date) - $t0).TotalSeconds
            Add-Content $SupLog "$(Get-Date -Format 'MM-dd HH:mm:ss') kit exited (code $($p.ExitCode)) after $secs s; last error lines:"
            Get-Content $ErrLog -Tail 3 -ErrorAction SilentlyContinue | ForEach-Object { Add-Content $SupLog "    $_" }
            if ($secs -lt $ShortRun) { $short++ } else { $short = 0 }
        } catch {
            Add-Content $SupLog "$(Get-Date -Format 'MM-dd HH:mm:ss') supervisor error: $_"
            $short++
        }
        if ($short -ge $ShortMax) { $wait = $BackoffWait; $short = 0 } else { $wait = $RestartWait }
        Add-Content $SupLog "$(Get-Date -Format 'MM-dd HH:mm:ss') restarting in $wait s"
        Start-Sleep -Seconds $wait
    }
}

# ---- pre-checks ----
foreach ($f in @($Py, $Kit, (Join-Path $Agent "agent.json"))) {
    if (-not (Test-Path $f)) { Write-Host "MISSING: $f"; exit 1 }
}
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

# ---- start the supervisor in background ----
Write-Host ("kit args: " + ($ArgList -join " "))
$p = Start-Process -FilePath "powershell.exe" -WindowStyle Hidden -PassThru `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Supervise")

$p.Id | Out-File $PidF -Encoding ascii
Write-Host "supervisor started (PID $($p.Id)); it restarts the kit when it exits. Watch: Get-Content $Log -Tail 10 -Wait"
Write-Host "Restarts: Get-Content $SupLog -Tail 10"
