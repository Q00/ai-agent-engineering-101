# Week-04 helper for Windows PowerShell. Run from the repository root:
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-04\run_week04.ps1 setup
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-04\run_week04.ps1 run
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-04\run_week04.ps1 check
# The API key is read from the environment only ($env:OPENROUTER_API_KEY). Never commit it.
param([Parameter(Mandatory = $true)][ValidateSet("setup", "run", "check")][string]$Step)
$ErrorActionPreference = "Stop"
$D = "submissions/26510127/week-04"
if (-not (Test-Path "$D/negotiate.py")) { throw "run this from the repository root" }

function Commit($paths, $msg) {
    git add -- $paths
    git diff --cached --quiet
    if ($LASTEXITCODE -ne 0) { git commit -m $msg | Out-Host } else { Write-Host "(nothing to commit: $msg)" }
}

switch ($Step) {
    "setup" {
        # 1) branch week-04 from the latest upstream main. Untracked files (the week-04 code, the
        #    uncommitted week-05 REPORT.md) stay in the working tree; only listed paths are committed.
        git fetch upstream
        git checkout -B week-04 upstream/main
        # 2) roster file: upstream does not have roster/26510127.md yet
        if (-not (Test-Path "roster/26510127.md")) {
            git checkout week-05 -- roster/26510127.md 2>$null
            if (-not (Test-Path "roster/26510127.md")) {
                Set-Content -Encoding utf8 roster/26510127.md "# 26510127`n`n- github: chaewoonee`n- name: Chaewon Lee"
            }
        }
        Commit "roster/26510127.md" "[roster] 26510127"
        # 3) one commit per unit of work; scenarios first (limits fixed before any run)
        Commit "$D/scenarios.json" "week-04: scenarios.json (4 scenarios, limits fixed before any run)"
        Commit @("$D/requirements.txt", "$D/run_week04.ps1") "week-04: requirements and Windows helper"
        Commit "$D/llm.py" "week-04: model call (OpenRouter gpt-oss-20b, retries, meter, mock for pipeline tests)"
        Commit "$D/acl.py" "week-04: role/act/format prompts and protocol layer (reader, tag regex, JSON parser)"
        Commit "$D/negotiate.py" "week-04: episode loop and resumable runner"
        python -m pip install -r "$D/requirements.txt"
        Write-Host "`nsetup done. Next: set the key, then run step 'run'."
    }
    "run" {
        foreach ($c in @("free", "tagged", "structured")) {
            python "$D/negotiate.py" --conditions $c --repeats 3
            if ($LASTEXITCODE -ne 0) { throw "negotiate.py stopped during $c (see the last [result] line)" }
            Commit @("$D/results.csv", "$D/logs") "week-04: runs for $c"
        }
    }
    "check" {
        python scripts/check_week04.py $D
    }
}
