param(
    [string]$ClaimCode = $env:MARKET_OWNER,
    [switch]$BuyerOnly,
    [switch]$ShopOnly,
    [string]$PublicUrl,
    [int]$Port = 0
)

$ErrorActionPreference = 'Stop'
if ($BuyerOnly -and $PublicUrl) { throw 'Choose buyer-only or a public shop URL, not both.' }
if ($BuyerOnly -and $ShopOnly) { throw 'Choose exactly one separate role.' }
if ($PublicUrl -and $Port -le 0) { throw 'A public URL requires the dedicated local proxy port.' }
$agentDir = Split-Path -Parent $PSScriptRoot
$repoRoot = (Resolve-Path (Join-Path $agentDir '../../..')).Path
$python = Join-Path $repoRoot '.venv/Scripts/python.exe'
$stateDir = Join-Path $agentDir '.market'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$statePath = Join-Path $stateDir $(if ($ShopOnly) { 'shop-session.json' } else { 'session.json' })
if (Test-Path -LiteralPath $statePath) {
    $previous = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if (Get-Process -Id $previous.pid -ErrorAction SilentlyContinue) {
        throw "Market kit is already running (PID $($previous.pid))."
    }
}
if (-not $ClaimCode) { throw 'Provide -ClaimCode or MARKET_OWNER to link the agents to GitHub.' }

$oldPath = $env:PATH
$oldKey = $env:OPENAI_API_KEY
$oldOwner = $env:MARKET_OWNER
$oldUtf8 = $env:PYTHONUTF8
$oldTunnelLog = $env:TUNNEL_LOGFILE
$oldTunnelLevel = $env:TUNNEL_LOGLEVEL
try {
    if (-not $env:OPENAI_API_KEY) {
        $credentialFile = Join-Path $agentDir '../week-06/.env'
        foreach ($line in [IO.File]::ReadAllLines($credentialFile)) {
            if ($line -match '^\s*OPENAI_API_KEY\s*=(.*)$') {
                $env:OPENAI_API_KEY = $Matches[1].Trim().Trim([char]34, [char]39)
            }
        }
    }
    if (-not $env:OPENAI_API_KEY) { throw 'Existing OpenAI credential is unavailable.' }
    $env:MARKET_OWNER = $ClaimCode
    $env:PYTHONUTF8 = '1'
    $env:PATH = (Join-Path $repoRoot '.venv/Scripts') + ';' + $oldPath
    $logDir = Join-Path $agentDir 'logs/market-sessions'
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
    $stdout = Join-Path $logDir "join-$stamp.log"
    $stderr = Join-Path $logDir "join-$stamp.err"
    # Info-level tunnel diagnostics contain no HTTP request headers.
    $env:TUNNEL_LOGFILE = Join-Path $logDir "cloudflared-$stamp.log"
    $env:TUNNEL_LOGLEVEL = 'info'
    $mode = if ($BuyerOnly) { '--buyer-only' } elseif ($PublicUrl) { '--public-url' } else { '--tunnel' }
    $kit = Join-Path $PSScriptRoot 'run-kit.py'
    $arguments = @('-u', ('"' + $kit + '"'), 'join', '--dir', ('"' + $agentDir + '"'),
        '--market', 'https://market.ourolabs.ai', '--name', 'seochanit', $mode)
    if ($PublicUrl) { $arguments += @($PublicUrl, '--port', $Port) }
    if ($ShopOnly) { $arguments += '--shop-only' }
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $repoRoot `
        -WindowStyle Hidden -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    [pscustomobject]@{pid=$process.Id; stdout=$stdout; stderr=$stderr; mode=$mode; port=$Port; public_url=$PublicUrl} |
        ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding utf8
    Write-Output "Started market kit (PID $($process.Id)); mode $mode"
    Write-Output "Output: $stdout"
    Write-Output "Errors: $stderr"
} finally {
    $env:PATH = $oldPath
    $env:OPENAI_API_KEY = $oldKey
    $env:MARKET_OWNER = $oldOwner
    $env:PYTHONUTF8 = $oldUtf8
    $env:TUNNEL_LOGFILE = $oldTunnelLog
    $env:TUNNEL_LOGLEVEL = $oldTunnelLevel
}
