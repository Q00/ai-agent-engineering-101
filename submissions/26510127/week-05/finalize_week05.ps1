# Submit the first week-05 run. Windows PowerShell, from the repository root, on branch week-05:
#   powershell -ExecutionPolicy Bypass -File submissions\26510127\week-05\finalize_week05.ps1
# Nothing is deleted: the aborted reruns move to previous/, the first run moves back, and the code
# is restored to the commit that produced the first run, each step in its own commit.
$ErrorActionPreference = "Stop"
$D = "submissions/26510127/week-05"
$RUN1 = "3608cf4"     # "week-05: runs for prompt_inject and server_inject"
if (-not (Test-Path "scripts")) { throw "run this from the repository root" }
if ((git rev-parse --abbrev-ref HEAD) -ne "week-05") { throw "switch to the week-05 branch first" }
git cat-file -e "$RUN1^{commit}"; if ($LASTEXITCODE -ne 0) { throw "commit $RUN1 not found" }

function Tracked($p) { $out = git ls-files -- $p; return [bool]$out }
function MoveInto($src, $dst) {
    if (-not (Test-Path $src)) { return }
    if (Tracked $src) { git mv $src $dst } else { Move-Item $src $dst; git add -- $dst }
}

# 1) keep the third (aborted) attempt
$P3 = "$D/previous/run3-aborted"
if ((Test-Path "$D/results.csv") -or (Test-Path "$D/logs")) {
    New-Item -ItemType Directory -Force $P3 | Out-Null
    MoveInto "$D/results.csv" "$P3/results.csv"
    MoveInto "$D/logs" "$P3/logs"
    MoveInto "$D/auth_checks.txt" "$P3/auth_checks.txt"
    git commit -m "week-05: keep aborted third run under previous/run3-aborted (OpenRouter returned a provider error inside an HTTP 200; buyer-first auth checks kept with it)" | Out-Host
}

# 2) submit the first run: its results, logs, auth checks, and the code that produced them
$P1 = "$D/previous/run1-seller-first"
git mv "$P1/results.csv" "$D/results.csv"
git mv "$P1/logs" "$D/logs"
git mv "$P1/auth_checks.txt" "$D/auth_checks.txt"
git checkout $RUN1 -- "$D/market_server.py" "$D/host.py" "$D/runner.py" "$D/auth_checks.py"
git commit -m "week-05: submit the first run (seller opened); restore its code so code and results match; reruns stay under previous/" | Out-Host

# 3) report and this script
git add -- "$D/REPORT.md" "$D/finalize_week05.ps1"
git commit -m "week-05: REPORT.md (first run, comparison with my week-04 run, aborted reruns)" | Out-Host

python scripts/check_week05.py $D
