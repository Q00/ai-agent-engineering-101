# Week 08 local machine bootstrap. No keys or claim codes are printed/committed.
# Run from a *new* PowerShell terminal AFTER stopping the previous market kit.
param([switch]$Join)

$ErrorActionPreference = "Stop"
Set-Location "C:\Users\jangs\ai-agent-engineering-101"

Write-Host "[1/6] Updating week-08 branch"
git fetch origin week-08
if ($LASTEXITCODE -ne 0) { throw "Cannot fetch week-08." }
git switch week-08
if ($LASTEXITCODE -ne 0) { throw "Cannot switch to week-08." }
git pull origin week-08
if ($LASTEXITCODE -ne 0) { throw "Cannot pull week-08." }

Write-Host "[2/6] Syncing official market kit v4 from Q00/main"
git fetch https://github.com/Q00/ai-agent-engineering-101.git main
if ($LASTEXITCODE -ne 0) { throw "Could not fetch upstream main." }
git merge --no-edit FETCH_HEAD
if ($LASTEXITCODE -ne 0) { throw "Git merge needs manual conflict resolution; do NOT continue." }

$kit = "scripts\market_kit.py"
$source = Get-Content $kit -Raw
if ($source -notmatch 'KIT_VERSION\s*=\s*["'']4["'']') {
    throw "Expected KIT_VERSION = 4. Do NOT join using an outdated kit."
}

$src = "submissions\26510128\week-07"
$dst = "submissions\26510128\week-08"
Write-Host "[3/6] Copying keys and user-owned private memory locally"
New-Item -ItemType Directory -Force "$dst\.market" | Out-Null
New-Item -ItemType Directory -Force "$dst\logs" | Out-Null
foreach ($key in @("shop_key.pem", "buyer_key.pem")) {
    if (-not (Test-Path "$src\$key")) { throw "Missing $key in week-07." }
    if (-not (Test-Path "$dst\$key")) {
        Copy-Item "$src\$key" "$dst\$key"
    }
}
if ((Test-Path "$src\.market\buyer-memory.json") -and
    -not (Test-Path "$dst\.market\buyer-memory.json")) {
    Copy-Item "$src\.market\buyer-memory.json" "$dst\.market\buyer-memory.json"
}
Write-Host "Keys copied privately. Their contents are NOT printed."

Write-Host "[4/6] Installing dependencies"
python -m pip install -r "$dst\requirements.txt"
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
Write-Host "[5/6] Running deterministic offline safety tests"
Push-Location $dst
try {
    python -m unittest test_round3_policy -v
    if ($LASTEXITCODE -ne 0) { throw "Offline safety tests failed; do NOT join." }
} finally { Pop-Location }

Write-Host "[6/6] Readiness"
Select-String -Path $kit -Pattern 'KIT_VERSION\s*=' | Select-Object -First 1
Write-Host "Week 08 code and local keys ready. Never commit *.pem or .market."
if ($Join) {
    $env:Path = "$env:LOCALAPPDATA\Programs\cloudflared;$env:Path"
    cloudflared --version
    if ($LASTEXITCODE -ne 0) { throw "cloudflared not available." }
    $env:MARKET_OWNER = Read-Host "Current private market claim code"
    try {
        python scripts/market_kit.py join --dir $dst --market https://market.ourolabs.ai --name JangSeongWoong --tunnel
    } finally {
        Remove-Item Env:\MARKET_OWNER -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "Run this file again with -Join only after verifying code/tests and stopping any old kit."
}
