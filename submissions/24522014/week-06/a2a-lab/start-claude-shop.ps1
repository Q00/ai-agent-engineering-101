# STEP 6: the lecture's one-shot Claude harness wrapped by the A2A CLI.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'use-a2a.ps1')
$step6Claude = Get-Command claude -ErrorAction Stop
$env:Path = (Split-Path $step6Claude.Source) + ';' + $env:Path
if (-not (Get-Command sh -ErrorAction SilentlyContinue)) {
    $step6Git = Get-Command git -ErrorAction Stop
    $step6GitBin = Join-Path (Split-Path (Split-Path $step6Git.Source)) 'bin'
    if (-not (Test-Path (Join-Path $step6GitBin 'sh.exe'))) {
        throw 'A2A CLI --exec needs sh.exe. Install Git for Windows or add sh to PATH.'
    }
    $env:Path = "$step6GitBin;$env:Path"
}
$step6Exec = "claude -p --model haiku --system-prompt 'You are a furniture shop. You sell lamps. Answer in English, in one short sentence.'"
# An empty working directory prevents the harness from reading this repo's instructions.
$step6WorkDir = Join-Path $PSScriptRoot 'harness-work'
New-Item -ItemType Directory -Force -Path $step6WorkDir | Out-Null
Push-Location $step6WorkDir
try {
    & $a2aExecutable server --exec $step6Exec --name 'Claude Shop' --port 8090
} finally {
    Pop-Location
}
