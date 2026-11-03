# Always-on deployment: keeps the shop kit (moss-and-oak) and the buyer kit (move_76) in the market.
#   powershell -NoProfile -ExecutionPolicy Bypass -File run_market.ps1
# Runs on the personal PC (Windows, Tailscale) as a scheduled task at startup, so it outlives SSH sessions.
# Secrets come from .env next to this file (git-ignored): MARKET_OWNER (claim code), OPENAI_API_KEY.
# The keys (shop_key.pem, buyer_key.pem) were copied over by scp, never committed.
# Round 3: the shop kit runs from shop/ (shop/agent.json: seller + restock) and the buyer kit from this folder, so
# each kit has its own .market/ and outbox.jsonl; with one shared outbox the shop kit posted the buyer's forum lines
# as the shop (market_kit.py outbox() falls back to its own role). The shop key moves into shop/ once, below: the kit
# would otherwise make a new key there, and a new key cannot join under the old name.
# A kit that exits is started again after 30 s; a restarted shop kit gets a new tunnel URL, which the kit
# registers under the same name and key, so trades and reviews carry on.
$ErrorActionPreference = "Continue"
$W = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = (Resolve-Path "$W\..\..\..").Path
$Rel = $W.Substring($Repo.Length + 1)

if (Test-Path "$W\.env") {
    Get-Content "$W\.env" | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' -and $matches[2].Trim()) {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim().Trim('"').Trim("'"), "Process")
        }
    }
}
$env:PYTHONUTF8 = "1"; $env:PYTHONIOENCODING = "utf-8"; $env:SELLER_RELOAD = "1"
$env:PATH = "$Repo\.venv\Scripts;$env:USERPROFILE\market\bin;$env:PATH"
New-Item -ItemType Directory -Force "$W\logs", "$W\shop" | Out-Null
if (-not (Test-Path "$W\shop\shop_key.pem")) {
    if (Test-Path "$W\shop_key.pem") { Copy-Item "$W\shop_key.pem" "$W\shop\shop_key.pem" }
    else { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') WARNING no shop_key.pem in $W or $W\shop: the kit will make a new shop key" | Add-Content "$W\logs\deploy.log" }
}
$Market = "https://market.ourolabs.ai"
$Kits = @{
    shop  = "scripts/market_kit.py join --dir $Rel/shop --market $Market --name moss-and-oak --tunnel --shop-only"
    buyer = "scripts/market_kit.py join --dir $Rel --market $Market --name move_76 --buyer-only"
}
$Procs = @{}

function Log($m) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $m" | Add-Content "$W\logs\deploy.log" }

Log "run_market start: repo $Repo, owner set: $([bool]$env:MARKET_OWNER), openai key set: $([bool]$env:OPENAI_API_KEY)"
while ($true) {
    foreach ($k in $Kits.Keys) {
        $p = $Procs[$k]
        if ($null -eq $p -or $p.HasExited) {
            if ($p) { Log "$k kit exited with $($p.ExitCode); restarting" }
            $Procs[$k] = Start-Process -FilePath "$Repo\.venv\Scripts\python.exe" -ArgumentList $Kits[$k] `
                -WorkingDirectory $Repo -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput "$W\logs\deploy-$k.out" -RedirectStandardError "$W\logs\deploy-$k.err"
            Log "$k kit started, pid $($Procs[$k].Id)"
        }
    }
    Start-Sleep -Seconds 30
}
