# Run in elevated Windows PowerShell as the Windows account owning the distro.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-z_][a-z0-9_-]*$')]
    [string]$LinuxUser,
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Distro = 'Ubuntu',
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$TaskName = 'OutseopMarketWSL'
)

$ErrorActionPreference = 'Stop'
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent()
$identity = $currentIdentity.Name
$description = 'Keep the authorized Ubuntu market runtime alive across SSH disconnects; market services are separately managed by systemd.'
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    # Task Scheduler may return "user" after registration as "COMPUTER\user".
    # Compare resolved identities, preserving the unrelated-task safeguard.
    $existingSid = ([Security.Principal.NTAccount]::new($existing.Principal.UserId)).Translate(
        [Security.Principal.SecurityIdentifier]).Value
    if (($existing.Description -ne $description) -or ($existingSid -ne $currentIdentity.User.Value)) {
        throw 'An unrelated scheduled task exists; it has been left unchanged.'
    }
}

$action = New-ScheduledTaskAction -Execute "$env:WINDIR\System32\wsl.exe" `
    -Argument "-d $Distro -u $LinuxUser --exec /usr/bin/sleep infinity"
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType S4U -RunLevel Highest
$triggers = @(
    (New-ScheduledTaskTrigger -AtStartup),
    (New-ScheduledTaskTrigger -AtLogOn -User $identity)
)
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings -Description $description -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Get-ScheduledTask -TaskName $TaskName | Select-Object TaskName, State

# Verify that the task remains Running and that sleep exists inside the distro.
# A Windows reboot/login recovery test is separate from this registration.
