# Week-05 helper for Windows PowerShell. Run from the repository root:
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-05\run_week05.ps1 setup
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-05\run_week05.ps1 run
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-05\run_week05.ps1 optional
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-05\run_week05.ps1 check
# The API key is read from the environment only ($env:OPENROUTER_API_KEY or $env:GROQ_API_KEY). Never commit it.
param([Parameter(Mandatory = $true)][ValidateSet("setup", "run", "optional", "check")][string]$Step)
$ErrorActionPreference = "Stop"
$D = "submissions/26510127/week-05"
if (-not (Test-Path "$D/market_server.py")) { throw "run this from the repository root" }

function Commit($paths, $msg) {
    git add -- $paths
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) { git commit -m $msg | Out-Host } else { Write-Host "(nothing to commit: $msg)" }
}

switch ($Step) {
    "setup" {
        # 1) branch week-05 from the latest upstream main (the week-05 files are untracked, so they carry over)
        git fetch upstream
        git checkout -B week-05 upstream/main
        # 2) roster file: upstream does not have roster/26510127.md yet; bring it from the week-03 branch
        if (-not (Test-Path "roster/26510127.md")) {
            git checkout week-03 -- roster/26510127.md 2>$null
            if (-not (Test-Path "roster/26510127.md")) {
                Set-Content -Encoding utf8 roster/26510127.md "# 26510127`n`n- github: chaewoonee`n- name: Chaewon Lee"
            }
            Commit "roster/26510127.md" "[roster] 26510127"
        }
        # 3) one commit per unit of work; scenarios first (limits fixed before any run)
        Commit "$D/scenarios.json" "week-05: scenarios.json (4 scenarios, limits fixed before any run)"
        Commit @("$D/requirements.txt", "$D/run_week05.ps1") "week-05: requirements and Windows helper"
        Commit "$D/market_server.py" "week-05: market MCP server (token identity, handle binding, turn check, token limits, buyer-only injection)"
        Commit "$D/host.py" "week-05: MCP host = week-01 loop over Streamable HTTP with bearer token"
        Commit "$D/runner.py" "week-05: runner (admin route mints tokens, resumable results.csv, one log per run)"
        Commit "$D/auth_checks.py" "week-05: auth checks script"
        python -m pip install -r "$D/requirements.txt"
        Write-Host "`nsetup done. Next: set the key, then run step 'run'."
    }
    "run" {
        python "$D/auth_checks.py"
        Commit "$D/auth_checks.txt" "week-05: auth_checks.txt from the running server"
        python "$D/runner.py" --conditions prompt_inject server_inject --repeats 3
        Commit @("$D/results.csv", "$D/logs") "week-05: runs for prompt_inject and server_inject"
    }
    "optional" {
        python "$D/runner.py" --conditions prompt server --repeats 3
        Commit @("$D/results.csv", "$D/logs") "week-05: runs for prompt and server (optional conditions)"
    }
    "check" {
        python scripts/check_week05.py $D
    }
}
