param(
    [switch]$BuyerOnly,
    [string]$PublicUrl,
    [int]$Port = 0,
    [ValidateSet('pinggy','cloudflare')][string]$TunnelProvider = 'pinggy'
)
$ErrorActionPreference = 'Stop'
$agentRoot = $PSScriptRoot
$pythonPath = Join-Path $agentRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run the environment setup in README.md first.' }
if ($BuyerOnly -and $PublicUrl) { throw 'Choose buyer-only or a public shop URL.' }
if ($PublicUrl -and $Port -le 0) { throw 'PublicUrl needs the dedicated proxy Port.' }
$claimFile = Join-Path $agentRoot '.market/owner.env'
$oldClaim = $env:MARKET_OWNER
$oldUtf8 = $env:PYTHONUTF8
$oldProvider = $env:MARKET_TUNNEL_PROVIDER
try {
    if (-not $env:MARKET_OWNER -and (Test-Path -LiteralPath $claimFile)) {
        foreach ($line in [IO.File]::ReadAllLines($claimFile)) {
            if ($line -match '^\s*MARKET_OWNER\s*=(.*)$') {
                $env:MARKET_OWNER = $Matches[1].Trim().Trim([char]34, [char]39)
            }
        }
    }
    if (-not $env:MARKET_OWNER) { throw 'Existing market owner credential is missing; run prepare_runtime.py.' }
    $env:PYTHONUTF8 = '1'
    $env:MARKET_TUNNEL_PROVIDER = $TunnelProvider
    $runnerPath = Join-Path $agentRoot 'run_market.py'
    $argsList = @($runnerPath, 'join', '--dir', $agentRoot, '--market', 'https://market.ourolabs.ai', '--name', 'seochanit')
    if ($BuyerOnly) { $argsList += '--buyer-only' }
    elseif ($PublicUrl) { $argsList += @('--public-url', $PublicUrl, '--port', $Port) }
    else { $argsList += '--tunnel' }
    & $pythonPath @argsList
    if ($LASTEXITCODE -ne 0) { throw "Market kit exited $LASTEXITCODE" }
} finally {
    $env:MARKET_OWNER = $oldClaim
    $env:PYTHONUTF8 = $oldUtf8
    $env:MARKET_TUNNEL_PROVIDER = $oldProvider
}
