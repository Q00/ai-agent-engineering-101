param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("install", "status", "remove")]
    [string]$Action
)

$ErrorActionPreference = "Stop"
$TaskName = "OuroMarket-25512088-Round3"
$SubmissionPath = "/home/ohs/projects/ai-agent-engineering-101/submissions/25512088/week-08"
$ExpectedArguments = "-d Ubuntu -- bash -lc `"cd '$SubmissionPath' && bash run_ubuntu.sh supervisor start`""
$ExpectedExecutable = "wsl.exe"

function Get-ExistingTask {
    return Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
}

function Test-ExpectedTask($Task) {
    if ($null -eq $Task) { return $false }
    if ($Task.Actions.Count -ne 1) { return $false }
    $taskAction = $Task.Actions[0]
    return (($taskAction.Execute -ieq $ExpectedExecutable) -and
            ($taskAction.Arguments -ceq $ExpectedArguments))
}

function Write-Result($State, $Matched) {
    [ordered]@{
        task_name = $TaskName
        action = $Action
        state = $State
        matched = $Matched
        executable = $ExpectedExecutable
        arguments = $ExpectedArguments
        submission_path = $SubmissionPath
    } | ConvertTo-Json -Compress
}

$existing = Get-ExistingTask
$matches = Test-ExpectedTask $existing

switch ($Action) {
    "install" {
        if ($null -ne $existing) {
            if (-not $matches) {
                Write-Result "conflict" $false
                exit 3
            }
            Write-Result "already_installed" $true
            exit 0
        }
        $taskAction = New-ScheduledTaskAction -Execute $ExpectedExecutable -Argument $ExpectedArguments
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew
        Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger `
            -Settings $settings -Description "Round 3 adaptive market supervisor for 25512088" | Out-Null
        $created = Get-ExistingTask
        if (-not (Test-ExpectedTask $created)) { throw "Created task did not match expected command" }
        Write-Result "installed" $true
    }
    "status" {
        if ($null -eq $existing) { Write-Result "absent" $false; exit 1 }
        if (-not $matches) { Write-Result "conflict" $false; exit 3 }
        Write-Result "installed" $true
    }
    "remove" {
        if ($null -eq $existing) { Write-Result "absent" $false; exit 0 }
        if (-not $matches) {
            Write-Result "conflict_preserved" $false
            exit 3
        }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Result "removed" $true
    }
}
