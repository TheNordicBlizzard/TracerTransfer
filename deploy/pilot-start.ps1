param([string]$Root='C:\ProgramData\TracerTransfer-Pilot')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$Root=[IO.Path]::GetFullPath($Root)
if(!(Test-Path -LiteralPath $Root)) { throw 'Prepare the isolated pilot directory first.' }
$state=Join-Path $Root 'processes.json'
if(Test-Path -LiteralPath $state) { throw 'State exists: inspect/stop previous pilot first.' }
foreach($port in @(8765,8766)) {
    if(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "Port occupied: $port" }
}
$bin=Join-Path $Root 'bin'
$data=Join-Path $Root 'data'
$logs=Join-Path $Root 'logs'
New-Item -ItemType Directory -Path $data,$logs -Force | Out-Null
$records=New-Object System.Collections.Generic.List[object]
function Start-PilotProcess($Name,$Arguments,$Order) {
    $exe=Join-Path $bin $Name
    $p=Start-Process -FilePath $exe -ArgumentList $Arguments -WorkingDirectory $Root -RedirectStandardOutput (Join-Path $logs ($Name+'.out.log')) -RedirectStandardError (Join-Path $logs ($Name+'.err.log')) -PassThru
    $p.Refresh()
    $records.Add([pscustomobject]@{Id=$p.Id;Path=$exe;StartTicks=$p.StartTime.ToUniversalTime().Ticks.ToString();Order=$Order})
    ConvertTo-Json -InputObject @($records.ToArray()) | Set-Content -LiteralPath $state -Encoding UTF8
}
function Wait-Health($Url) {
    $deadline=(Get-Date).AddSeconds(45)
    do {
        try { if((Invoke-RestMethod -Uri $Url -Headers @{Host='pilot-origin.invalid'} -TimeoutSec 2).ok) { return } } catch {}
        Start-Sleep -Milliseconds 500
    } while((Get-Date) -lt $deadline)
    throw "Health timeout: $Url"
}
try {
    & (Join-Path $bin 'caddy.exe') validate --config (Join-Path $Root 'Caddyfile.pilot') --adapter caddyfile
    if($LASTEXITCODE -ne 0) { throw 'Invalid proxy configuration' }
    $bootstrap=Join-Path $Root 'bootstrap.json'
    $args='--headless --host 127.0.0.1 --port 8765 --data-dir "'+$data+'"'
    if(!(Test-Path (Join-Path $data 'tracer.sqlite3'))) {
        $rng=[Security.Cryptography.RandomNumberGenerator]::Create()
        $bytes=New-Object byte[] 32
        $rng.GetBytes($bytes);$rng.Dispose()
        $password=[Convert]::ToBase64String($bytes)
        @{username='pilot-admin';password=$password} | ConvertTo-Json | Set-Content -LiteralPath $bootstrap -Encoding UTF8
        Add-Type -AssemblyName System.Security
        # Pilot-only machine scope; root ACL grants only SYSTEM and administrators.
        $encrypted=[Security.Cryptography.ProtectedData]::Protect([Text.Encoding]::UTF8.GetBytes($password),$null,[Security.Cryptography.DataProtectionScope]::LocalMachine)
        [IO.File]::WriteAllBytes((Join-Path $Root 'admin.dpapi'),$encrypted)
        $password=$null
        $args+=' --bootstrap-file "'+$bootstrap+'"'
    }
    Start-PilotProcess 'TracerServidor.exe' $args 1
    Wait-Health 'http://127.0.0.1:8765/health'
    Start-PilotProcess 'caddy.exe' ('run --config "'+(Join-Path $Root 'Caddyfile.pilot')+'" --adapter caddyfile') 2
    Wait-Health 'http://127.0.0.1:8766/health'
    foreach($path in @('/','/api/login','/api/users','/api/status')) {
        try { Invoke-WebRequest -UseBasicParsing -Uri ('http://127.0.0.1:8766'+$path) -TimeoutSec 3 | Out-Null; throw "Public route allowed: $path" }
        catch { if(!$_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 404) { throw } }
    }
    Start-PilotProcess 'cloudflared.exe' 'tunnel --url http://127.0.0.1:8766 --no-autoupdate' 3
    Write-Output 'PASS: backend and restricted proxy healthy; Quick Tunnel process started.'
} catch {
    $failure=$_
    if(Test-Path $state) { & (Join-Path $Root 'pilot-stop.ps1') -Root $Root }
    throw $failure
} finally {
    if(Test-Path (Join-Path $Root 'bootstrap.json')) { Remove-Item -LiteralPath (Join-Path $Root 'bootstrap.json') }
}
