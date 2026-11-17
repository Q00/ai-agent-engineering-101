# Register the separate minutely availability watchdog task. Same user, Interactive, Limited; no password, no
# elevation, no power-policy change. The main task FairFurniture-Week08-Round3 is never modified or started here.
param([switch]$Replace)
$ErrorActionPreference = 'Stop'
$TaskName = 'FairFurniture-Week08-Round3-Watchdog'
$Repo = "$HOME\ai-agent-engineering-101"
$Script = Join-Path $PSScriptRoot 'availability_watchdog.py'
$Pythonw = "$Repo\.venv\Scripts\pythonw.exe"
foreach ($p in $Script, $Pythonw) { if (-not (Test-Path $p)) { throw "Missing: $p" } }
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing -and -not $Replace) { throw 'Watchdog task already exists; rerun with -Replace to re-register it.' }
if ($Existing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
$Action = New-ScheduledTaskAction -Execute $Pythonw -Argument "`"$Script`"" -WorkingDirectory $PSScriptRoot
$Minutely = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$AtLogOn = New-ScheduledTaskTrigger -AtLogOn -User $User
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger @($Minutely, $AtLogOn) -Principal $Principal `
    -Settings $Settings -Description 'Silent availability watchdog: starts FairFurniture-Week08-Round3 only when no owned supervisor exists; never kills.' | Out-Null
$t = Get-ScheduledTask -TaskName $TaskName
[pscustomobject]@{
    TaskName = $t.TaskName; State = "$($t.State)"; Execute = $t.Actions.Execute; Arguments = $t.Actions.Arguments
    LogonType = "$($t.Principal.LogonType)"; RunLevel = "$($t.Principal.RunLevel)"
    MultipleInstances = "$($t.Settings.MultipleInstances)"; StartWhenAvailable = $t.Settings.StartWhenAvailable
    DisallowStartIfOnBatteries = $t.Settings.DisallowStartIfOnBatteries; StopIfGoingOnBatteries = $t.Settings.StopIfGoingOnBatteries
    ExecutionTimeLimit = $t.Settings.ExecutionTimeLimit
    Triggers = @($t.Triggers | ForEach-Object { "$($_.CimClass.CimClassName) interval=$($_.Repetition.Interval) duration=$($_.Repetition.Duration)" })
} | ConvertTo-Json
