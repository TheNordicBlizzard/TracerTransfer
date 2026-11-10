$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
Add-Type -AssemblyName System.Security
$blob=[IO.File]::ReadAllBytes('C:\ProgramData\TracerTransfer-Pilot\admin.dpapi')
$password=[Text.Encoding]::UTF8.GetString([Security.Cryptography.ProtectedData]::Unprotect($blob,$null,[Security.Cryptography.DataProtectionScope]::LocalMachine))
$session=Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/login -ContentType application/json -Body (@{username='pilot-admin';password=$password}|ConvertTo-Json -Compress)
$password=$null
$headers=@{Authorization=('Bearer '+$session.token)}
try {
    $devices=Invoke-RestMethod -Uri http://127.0.0.1:8765/api/devices -Headers $headers
    $count=0
    foreach($device in $devices) {
        if($device.name -eq 'synthetic-pilot-exe' -and $device.enabled) {
            Invoke-RestMethod -Method Delete -Uri ('http://127.0.0.1:8765/api/devices/'+$device.id) -Headers $headers | Out-Null
            $count++
        }
    }
    $status=Invoke-RestMethod -Uri http://127.0.0.1:8765/api/status -Headers $headers
    Write-Output ('Revoked test devices: '+$count)
    [ordered]@{Devices=$status.devices;Files=$status.files} | ConvertTo-Json -Depth 4
} finally {
    Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8765/api/logout -Headers $headers | Out-Null
}
