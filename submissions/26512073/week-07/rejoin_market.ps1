param(
    [Parameter(Mandatory = $true)]
    [string]$PublicUrl
)

$ErrorActionPreference = 'Stop'
if ($PublicUrl -notmatch '^https://[a-z0-9-]+\.trycloudflare\.com/?$') {
    throw 'Provide the actual HTTPS trycloudflare.com URL printed by your tunnel.'
}

$weekDir = $PSScriptRoot
$pythonExe = Join-Path $weekDir '.venv/Scripts/python.exe'
$kit = Join-Path $weekDir 'course_support/market_kit.py'

# Live runs must use the existing identity and live pricing/buyer memory.
Remove-Item Env:SELLER_DATA_DIR -ErrorAction SilentlyContinue
Remove-Item Env:BUYER_MEMORY_PATH -ErrorAction SilentlyContinue
if (-not $env:MARKET_OWNER) {
    $claim = Read-Host 'Paste your private market claim code' -AsSecureString
    $env:MARKET_OWNER = [System.Net.NetworkCredential]::new('', $claim).Password
    $claim.Dispose()
}

& $pythonExe $kit join --dir $weekDir --market 'https://market.ourolabs.ai' `
    --name 'Afford Shop' --port 8900 --public-url $PublicUrl
exit $LASTEXITCODE
