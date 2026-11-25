# ops: starting the two runtimes

| Task Scheduler item | Script | What runs |
|---|---|---|
| 1 inner | `ops\inner.ps1` | kit v4 (`--name gom31-honest-shop --tunnel`) and its hooks from `agent.json` |
| 2 outer | `ops\outer.ps1` | `outer.py`: collection every 3 min, forum every 30 min, a slice every even hour, the audit |

Register them yourself (the agent does not change scheduled tasks), for example:

```powershell
$w = "C:\Users\gom12\ax\ai-agent-engineering-101\submissions\26512070\week-08"
foreach ($n in "inner", "outer") {
  $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File $w\ops\$n.ps1"
  $s = New-ScheduledTaskSettingsSet -RestartCount 99 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0 -StartWhenAvailable
  Register-ScheduledTask -TaskName "market-$n" -Action $a -Trigger (New-ScheduledTaskTrigger -AtLogOn) -Settings $s
}
```

Before item 1, do appendix C steps 1-5 of HARNESS.md (keys into week-08, legacy copies, no logs). Only one
machine may run the kit. The laptop must not sleep for the round (power settings).
