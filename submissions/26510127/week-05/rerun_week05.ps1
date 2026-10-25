# Week-05 rerun (buyer opens, reasoning logged, OpenRouter provider pinned). Windows PowerShell,
# from the repository root:
#   powershell -ExecutionPolicy Bypass -File _week05_update\rerun_week05.ps1 prepare
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-05\rerun_week05.ps1 run
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-05\rerun_week05.ps1 check
# The API key is read from the environment only ($env:OPENROUTER_API_KEY). Never commit it.
#   (after an aborted run)  ...\rerun_week05.ps1 restart   then   ...\rerun_week05.ps1 run
param([Parameter(Mandatory = $true)][ValidateSet("prepare", "restart", "run", "check")][string]$Step)
$ErrorActionPreference = "Stop"
$D = "submissions/26510127/week-05"
$U = "_week05_update"
if (-not (Test-Path "scripts")) { throw "run this from the repository root" }

function Commit($paths, $msg) {
    git add -- $paths
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) { git commit -m $msg | Out-Host } else { Write-Host "(nothing to commit: $msg)" }
}
function Native($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

switch ($Step) {
    "prepare" {
        if (-not (Test-Path "$U/market_server.py")) { throw "$U is missing; run 'prepare' before deleting it" }
        git checkout week-05; Native "git checkout week-05"
        # 1) keep the first run, do not delete it: it is part of the record
        if (Test-Path "$D/results.csv") {
            $P = "$D/previous/run1-seller-first"
            New-Item -ItemType Directory -Force $P | Out-Null
            git mv "$D/results.csv" "$P/results.csv"; Native "git mv results.csv"
            git mv "$D/logs" "$P/logs"; Native "git mv logs"
            git mv "$D/auth_checks.txt" "$P/auth_checks.txt"; Native "git mv auth_checks.txt"
            git commit -m "week-05: keep first run under previous/run1-seller-first (seller opened; the lecture says the buyer opens)" | Out-Host
        }
        # 2) one commit per change
        Copy-Item "$U/market_server.py" "$D/" -Force
        Commit "$D/market_server.py" "week-05: buyer opens the negotiation, then the parties alternate"
        Copy-Item "$U/auth_checks.py" "$D/" -Force
        Commit "$D/auth_checks.py" "week-05: auth checks follow the buyer-first order"
        Copy-Item "$U/host.py" "$D/" -Force
        Commit "$D/host.py" "week-05: host logs model reasoning (never fed back) and pins the OpenRouter provider"
        Copy-Item "$U/runner.py" "$D/" -Force
        Commit "$D/runner.py" "week-05: runner counts buyer notice mentions in reasoning and logs settings per episode"
        Copy-Item "$U/rerun_week05.ps1" "$D/" -Force
        Commit "$D/rerun_week05.ps1" "week-05: rerun helper"
        python -m pip install -r "$D/requirements.txt"
        Remove-Item -Recurse -Force $U
        Write-Host "`nprepare done. Next (same window, key set): $D\rerun_week05.ps1 run"
    }
    "restart" {
        # keep the aborted attempt (uncommitted results and logs) as a record, then commit the fixes
        if (Test-Path "$D/results.csv") {
            $P = "$D/previous/run2-aborted"
            New-Item -ItemType Directory -Force $P | Out-Null
            Move-Item "$D/results.csv" "$P/results.csv"
            Move-Item "$D/logs" "$P/logs"
            Commit $P "week-05: keep aborted second run under previous/run2-aborted (turns passed when the model stopped without a tool call; then an LLM error aborted the run)"
        }
        Commit "$D/host.py" "week-05: host reminds the model once when it stops without a tool call; a malformed tool call ends the turn instead of aborting the run"
        Commit "$D/runner.py" "week-05: runner records reminders and malformed tool calls per episode"
        Commit "$D/rerun_week05.ps1" "week-05: rerun helper gets a restart step"
        Write-Host "`nrestart done. Next (key set): $D\rerun_week05.ps1 run"
    }
    "run" {
        if (-not (Test-Path "$D/auth_checks.txt")) {
            python "$D/auth_checks.py"; Native "auth_checks.py"
            Commit "$D/auth_checks.txt" "week-05: auth_checks.txt (buyer-first server)"
        }
        foreach ($c in @("prompt_inject", "server_inject", "prompt", "server")) {
            python "$D/runner.py" --conditions $c --repeats 3
            Native "runner.py ($c)"
            Commit @("$D/results.csv", "$D/logs") "week-05: rerun $c (buyer first, pinned provider, reasoning logged)"
        }
    }
    "check" {
        python scripts/check_week05.py $D
    }
}
