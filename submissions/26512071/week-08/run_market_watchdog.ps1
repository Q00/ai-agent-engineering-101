# Keep the existing external tunnel; supervise ONLY this week-08 kit tree.
param([Parameter(Mandatory=$true)][string]$PublicUrl)
$ErrorActionPreference = 'Stop'
$Repo = "$HOME\ai-agent-engineering-101"
$Log = "$HOME\agent-market"
$Dir = 'submissions/26512071/week-08'
Set-Location $Repo
# Reuse trusted launcher environment read-only. Never emit its credential-bearing lines.
foreach ($line in [IO.File]::ReadAllLines("$Log\rejoin.ps1")) {
    if ($line -match '^\$env:[A-Za-z_]+\s*=') { Invoke-Expression $line | Out-Null }
}
$env:PATH = "$Repo\.venv\Scripts;$env:PATH"
$PID | Set-Content "$Repo\$Dir\logs\trading-supervisor.pid"
while ($true) {
    try {
        $arguments = @('scripts/market_kit.py','join','--dir',$Dir,'--market','https://market.ourolabs.ai',
                       '--name','fair-furniture','--port','8900','--public-url',$PublicUrl)
        $kit = Start-Process "$Repo\.venv\Scripts\python.exe" -WindowStyle Hidden -PassThru `
            -ArgumentList $arguments `
            -RedirectStandardOutput "$Log\join.log" -RedirectStandardError "$Log\join.err"
        $kit.Id | Set-Content "$Log\kit.pid"
        Wait-Process -Id $kit.Id -ErrorAction SilentlyContinue
    } catch {
        # No exception detail: launcher credentials must never be logged.
        Add-Content "$Repo\$Dir\logs\trading-supervisor.log" 'Kit start/wait failed; retry in 10 seconds.'
    }
    Start-Sleep 10
}
