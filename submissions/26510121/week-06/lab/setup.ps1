param([string]$Python = "python")
$ErrorActionPreference = "Stop"
$submission = Split-Path $PSScriptRoot -Parent
$runtime = Join-Path $submission ".runtime"
$logRoot = Join-Path $submission "logs/lab"
New-Item -ItemType Directory -Force $runtime, $logRoot, (Join-Path $runtime "tmp") | Out-Null
$env:TEMP = Join-Path $runtime "tmp"
$env:TMP = $env:TEMP
$env:PIP_DISABLE_PIP_VERSION_CHECK = "1"
$stamp = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss-fff")
Start-Transcript -Path (Join-Path $logRoot "setup-$stamp.log") | Out-Null
try {
    $venvPython = Join-Path $submission ".venv/Scripts/python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) {
        & $Python -m venv (Join-Path $submission ".venv")
        if ($LASTEXITCODE -ne 0) { throw "Failed to create the virtual environment." }
    }
    & $venvPython -m pip install --no-cache-dir -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "Failed to install Python dependencies." }

    if ([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne "X64") {
        throw "This setup pins the Windows x64 CLI. Use the official release for your architecture."
    }
    $asset = "a2a_0.3.0_windows_amd64.zip"
    $url = "https://github.com/a2aproject/a2a-cli/releases/download/v0.3.0/$asset"
    $expected = "169f66ea6d1ec52096396335453a77d5a25e5af1336945f233f0e5d09b1377c2"
    $archive = Join-Path $runtime $asset
    Invoke-WebRequest -Uri $url -OutFile $archive
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $expected) { throw "A2A CLI archive SHA256 mismatch: $actual" }
    Write-Output "A2A CLI archive SHA256 verified: $actual"
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $runtime "a2a") -Force
    $cli = Join-Path $runtime "a2a/a2a.exe"
    & $cli version
    if ($LASTEXITCODE -ne 0) { throw "A2A CLI version command failed." }
    & $venvPython --version
    & $venvPython -m pip freeze | Set-Content -LiteralPath (Join-Path $PSScriptRoot "requirements-lock.txt") -Encoding utf8NoBOM
    Write-Output "Ready. Run: .\.venv\Scripts\python.exe lab\run_lab.py"
} finally {
    Stop-Transcript | Out-Null
}
