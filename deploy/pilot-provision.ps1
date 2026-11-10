$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Add-Type -AssemblyName System.Security
$root='C:\ProgramData\TracerTransfer-Pilot'
$blob=[IO.File]::ReadAllBytes("$root\admin.dpapi")
$password=[Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect($blob,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine))
$body=@{username='pilot-admin';password=$password} | ConvertTo-Json -Compress
$session=Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/login -ContentType application/json -Body $body
$password=$null;$body=$null
$headers=@{Authorization=('Bearer '+$session.token)}
try {
    $device=Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/devices -ContentType application/json -Headers $headers -Body '{"name":"synthetic-pilot-exe"}'
    # Consumed programmatically through SSH, never print this output to chat/logs.
    Write-Output ('PILOT_DEVICE:'+($device | ConvertTo-Json -Compress))
} finally {
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/logout -Headers $headers | Out-Null
}
