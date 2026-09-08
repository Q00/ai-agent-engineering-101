param(
    [int]$Runs = 3,
    [int]$MaxSteps = 16,
    [switch]$Check,
    [ValidateSet('v1', 'v2')][string]$PlanPrompt = 'v1',
    [ValidateSet('strict', 'tolerant')][string]$PlanParser = 'strict',
    [ValidateSet('both', 'react', 'plan_exec')][string]$Only = 'both'
)
$ErrorActionPreference = 'Stop'
$experimentPython = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $experimentPython)) {
    throw 'Create the student virtual environment and install requirements.txt first. See README.md.'
}
$previousKey = $env:OPENROUTER_API_KEY
$previousEncoding = $env:PYTHONIOENCODING
try {
    $env:PYTHONIOENCODING = 'utf-8'
    if (-not $Check -and -not $env:OPENROUTER_API_KEY -and -not $env:OPENAI_API_KEY) {
        $secureExperimentKey = Read-Host 'OpenRouter API key (hidden; this process only)' -AsSecureString
        $env:OPENROUTER_API_KEY = [System.Net.NetworkCredential]::new('', $secureExperimentKey).Password
    }
    $runnerArguments = @((Join-Path $PSScriptRoot 'run_ab.py'), '--runs', $Runs, '--max-steps', $MaxSteps,
                         '--plan-prompt', $PlanPrompt, '--plan-parser', $PlanParser, '--only', $Only)
    if ($Check) { $runnerArguments += '--check' }
    & $experimentPython @runnerArguments
    $experimentExitCode = $LASTEXITCODE
}
finally {
    $env:OPENROUTER_API_KEY = $previousKey
    $env:PYTHONIOENCODING = $previousEncoding
    if ($secureExperimentKey) { $secureExperimentKey.Dispose() }
}
exit $experimentExitCode
