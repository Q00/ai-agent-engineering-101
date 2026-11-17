# Scheduled-task action: import the trusted launcher's $env: lines silently, then run the supervisor in the
# foreground so this task instance lives exactly as long as market_runtime.py. Extra arguments (e.g.
# --public-url https://...) are passed through. Nothing here ever prints the imported values.
$ErrorActionPreference = 'Stop'
$Repo = "$HOME\ai-agent-engineering-101"
$Trusted = "$HOME\agent-market\rejoin.ps1"
Set-Location $Repo
$imported = $true
foreach ($line in [IO.File]::ReadAllLines($Trusted)) {
    if ($line -match '^\$env:[A-Za-z_][A-Za-z0-9_]*\s*=') {
        # No error detail: a failing line's text could carry a credential.
        try { Invoke-Expression $line | Out-Null } catch { $imported = $false }
    }
}
if (-not $imported) { exit 5 }
# Task Scheduler hands over a stale environment; a nonempty User-scope key wins, a missing or empty one keeps
# the inherited key. No error detail, and the value is never printed.
try {
    $userKey = Get-ItemPropertyValue -Path 'HKCU:\Environment' -Name OPENAI_API_KEY -ErrorAction Stop
    if ($userKey -is [string] -and $userKey -ne '') { $env:OPENAI_API_KEY = $userKey }
} catch { }
Remove-Variable -Name userKey -ErrorAction SilentlyContinue
& "$Repo\.venv\Scripts\python.exe" "$Repo\submissions\26512071\week-08\market_runtime.py" --exit-with-parent --parent-pid $PID @args
exit $LASTEXITCODE
