# exit 0: no python.exe whose command line contains the pattern is running (safe to start one)
# exit 1: one is running, or the check itself failed (fail safe: the caller skips, never starts a second copy)
param([Parameter(Mandatory = $true)][string]$Pattern)
try {
    $found = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
        Where-Object { $_.CommandLine -like "*$Pattern*" }
    if ($found) { exit 1 } else { exit 0 }
} catch {
    exit 1
}
