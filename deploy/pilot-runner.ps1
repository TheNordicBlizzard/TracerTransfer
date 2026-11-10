$ErrorActionPreference='Stop'
$root='C:\ProgramData\TracerTransfer-Pilot'
Start-Transcript -Path "$root\runner.log" -Append | Out-Null
try {
    & "$root\pilot-start.ps1" -Root $root
    $items=Get-Content "$root\processes.json" -Raw | ConvertFrom-Json
    $tunnel=$items | Where-Object { $_.Order -eq 3 }
    if(!$tunnel){throw 'No tunnel PID in pilot state'}
    Wait-Process -Id $tunnel.Id
} catch {
    $_ | Out-String | Add-Content "$root\runner-error.log"
    throw
} finally {
    if(Test-Path "$root\processes.json") { & "$root\pilot-stop.ps1" -Root $root }
    Stop-Transcript | Out-Null
}
