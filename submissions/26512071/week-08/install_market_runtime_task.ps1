# Register only the task-owned Week08 deployment; no account passwords or elevated principal.
$ErrorActionPreference = 'Stop'
$TaskName = 'FairFurniture-Week08-Round3'
$Launcher = Join-Path $PSScriptRoot 'launch_market_runtime.ps1'
if (-not (Test-Path $Launcher)) { throw 'Verified launcher is required before registration.' }
$User = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Launcher`"" -WorkingDirectory $PSScriptRoot
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
$Settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval ([TimeSpan]::FromMinutes(1)) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Existing) { throw 'Task already exists; inspect exact task before making changes.' }
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Principal $Principal -Settings $Settings -Description 'User-authorized Week08 Round3 market supervisor; managed tunnel, exact-owned kit jobs, append-only lifecycle evidence.' | Out-Null
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName,State,@{Name='Execute';Expression={$_.Actions.Execute}},@{Name='Arguments';Expression={$_.Actions.Arguments}},@{Name='ExecutionTimeLimit';Expression={$_.Settings.ExecutionTimeLimit}},@{Name='RestartCount';Expression={$_.Settings.RestartCount}},@{Name='LogonType';Expression={$_.Principal.LogonType}} | ConvertTo-Json
Start-ScheduledTask -TaskName $TaskName
