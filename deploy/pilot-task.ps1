$ErrorActionPreference='Stop'
$root='C:\ProgramData\TracerTransfer-Pilot'
$name='TracerTransfer-Pilot-Temporary'
if(Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue){throw 'Pilot task exists; inspect before reuse'}
# SSH job objects terminate child processes on disconnect. Use an on-demand,
# non-startup task, with a two-hour cap. No password, service or logon trigger.
if(Test-Path "$root\processes.json"){& "$root\pilot-stop.ps1" -Root $root}
$action=New-ScheduledTaskAction -Execute "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\ProgramData\TracerTransfer-Pilot\pilot-runner.ps1' -WorkingDirectory $root
$principal=New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Principal $principal -Settings $settings -Description 'Temporary synthetic-data pilot. On demand only. Delete after testing.' | Out-Null
Start-ScheduledTask -TaskName $name
Write-Output 'Started on-demand pilot task; no startup/logon trigger; two-hour limit.'
