# Dot-source this file to update PATH in the current PowerShell session.
$a2aInstallDir = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages\a2aproject.a2acli_Microsoft.Winget.Source_8wekyb3d8bbwe'
$a2aExecutable = Join-Path $a2aInstallDir 'a2a.exe'
if (-not (Test-Path -LiteralPath $a2aExecutable -PathType Leaf)) {
    throw "A2A CLI not found at $a2aExecutable. Install it with winget first."
}
if (($env:Path -split ';') -notcontains $a2aInstallDir) {
    $env:Path = "$a2aInstallDir;$env:Path"
}
& $a2aExecutable version
