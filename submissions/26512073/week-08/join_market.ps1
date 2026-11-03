param([switch]$Check)

$ErrorActionPreference = 'Stop'
$weekDir = $PSScriptRoot
$repoDir = (Resolve-Path (Join-Path $weekDir '../../..')).Path
$pythonExe = Join-Path $weekDir '.venv/Scripts/python.exe'
$kit = Join-Path $repoDir 'scripts/market_kit.py'
$cloudflaredDir = Join-Path $repoDir 'submissions/26512073/week-07/.tools'
$env:PYTHONUTF8 = '1'
if (Test-Path -LiteralPath (Join-Path $cloudflaredDir 'cloudflared.exe')) {
    $env:PATH = "$cloudflaredDir;$env:PATH"
}
if (-not (Test-Path -LiteralPath $pythonExe)) { throw 'Week 8 Python environment is missing.' }
if (-not (Test-Path -LiteralPath $kit)) { throw 'Repository market kit is missing.' }

Push-Location $weekDir
try {
    & $pythonExe -c "import json, pathlib, harness, seller, market_buyer; r=json.loads(pathlib.Path('round3_rules.json').read_text()); assert r['enabled'], 'Round 3 rules are disabled'; assert r['stock_budget']==2000000; assert pathlib.Path('shop_key.pem').exists() and pathlib.Path('buyer_key.pem').exists(); assert harness.find_codex(); print('Week 8 imports, rules, local identities and Codex executable: OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Week 8 preflight failed.' }
    & $pythonExe $kit join --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Market kit import failed.' }
    if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
        throw 'cloudflared is missing; the shop needs a public tunnel.'
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
