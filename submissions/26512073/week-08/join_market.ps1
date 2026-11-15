param([switch]$Check, [switch]$Standalone)

$ErrorActionPreference = 'Stop'
$weekDir = $PSScriptRoot
$repoDir = (Resolve-Path (Join-Path $weekDir '../../..')).Path
$pythonExe = Join-Path $weekDir '.venv/Scripts/python.exe'
$kit = Join-Path $weekDir 'market_runner.py'
if ($Standalone) { $kit = Join-Path $weekDir 'standalone_shop.py' }
$cloudflaredDir = Join-Path $repoDir 'submissions/26512073/week-07/.tools'
$env:PYTHONUTF8 = '1'
if (Test-Path -LiteralPath (Join-Path $cloudflaredDir 'cloudflared.exe')) {
    $env:PATH = "$cloudflaredDir;$env:PATH"
}
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Week 8 Python environment is missing.' }
if (-not (Test-Path -LiteralPath $kit)) { throw 'Repository market kit is missing.' }

Push-Location $weekDir
try {
    $requiredKeys = @('shop_key.pem')
    if (-not $Standalone) { $requiredKeys += 'buyer_key.pem' }
    foreach ($keyFile in $requiredKeys) {
        if (-not (Test-Path -LiteralPath $keyFile)) { throw "Local identity file is missing: $keyFile" }
    }
    & $pythonExe -c "import json, pathlib, harness, seller, market_buyer; r=json.loads(pathlib.Path('round3_rules.json').read_text()); assert r['enabled'], 'Round 3 rules are disabled'; assert r['stock_budget']==4000000; assert harness.find_codex(); print('Week 8 imports, rules, local identities and Codex executable: OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Week 8 preflight failed.' }
    & $pythonExe $kit join --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Market kit import failed.' }
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        throw 'cloudflared is missing; the shop needs a public tunnel.'
    }
    if ($Standalone) {
        & $pythonExe -c "import standalone_shop; from seller import build; build(8900); print('Student checkout imports and saved state: OK')"
        if ($LASTEXITCODE -ne 0) { throw 'Standalone checkout preflight failed.' }
    }
    if ($Check) { Write-Host 'Preflight complete. No market process started.'; return }

    Remove-Item Env:SELLER_DATA_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:BUYER_MEMORY_PATH -ErrorAction SilentlyContinue
    if (-not $env:MARKET_OWNER) {
        $claim = Read-Host 'Paste your private market claim code' -AsSecureString
        try { $env:MARKET_OWNER = [System.Net.NetworkCredential]::new('', $claim).Password }
        finally { $claim.Dispose() }
    }
    & $pythonExe $kit join --dir $weekDir --market 'https://market.ourolabs.ai' --name 'Afford Shop' --tunnel
    if ($LASTEXITCODE -ne 0) { throw "Market kit exited with code $LASTEXITCODE" }
}
finally { Pop-Location }
