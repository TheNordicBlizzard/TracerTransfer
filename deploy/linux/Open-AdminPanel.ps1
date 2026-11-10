param([string]$VMHost='192.0.2.10',[int]$LocalPort=18765,[string]$KeyPath,[string]$KnownHosts,[switch]$NoBrowser)
$ErrorActionPreference='Stop'
if ($VMHost -eq '192.0.2.10' -and (Test-Path (Join-Path $PSScriptRoot '..\..\deployment.local.json'))) {
    try {
        $cfg = Get-Content (Join-Path $PSScriptRoot '..\..\deployment.local.json') -Raw | ConvertFrom-Json
        if ($cfg.admin_ssh_host) { $VMHost = ($cfg.admin_ssh_host -split '@')[-1] }
    } catch {}
}
if($LocalPort -lt 1024 -or $LocalPort -gt 65535){throw 'Invalid local port'}
if(!$KeyPath){$KeyPath=Join-Path $HOME '.ssh\id_ed25519_tracer_vm'}
if(!$KnownHosts){$KnownHosts=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\tracer-vm.known_hosts'))}
foreach($path in @($KeyPath,$KnownHosts)){if(!(Test-Path -LiteralPath $path -PathType Leaf)){throw 'Administrative SSH identity/known_hosts missing'}}
if($VMHost -notmatch '^[a-zA-Z0-9.-]+$'){throw 'Invalid VM host'}
$ssh=(Get-Command ssh.exe -ErrorAction Stop).Source
$listener=New-Object Net.Sockets.TcpListener ([Net.IPAddress]::Loopback),$LocalPort
try{$listener.Start()}catch{throw 'Local port already in use; close the previous panel access first'}finally{$listener.Stop()}
$diagnostics=Join-Path $env:LOCALAPPDATA 'TracerTransfer\admin-access'
New-Item -ItemType Directory -Path $diagnostics -Force|Out-Null
$log=Join-Path $diagnostics ('ssh-'+[guid]::NewGuid().ToString('N')+'.log')
$values=@('-N','-i',$KeyPath,'-o',('UserKnownHostsFile='+$KnownHosts),'-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ExitOnForwardFailure=yes','-o','ConnectTimeout=8','-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3','-L',("127.0.0.1:${LocalPort}:127.0.0.1:8765"),('traceradmin@'+$VMHost))
# Each value is a separate Windows argv; reject embedded quotes, never interpolate a shell command.
foreach($value in $values){if($value.Contains('"')){throw 'Invalid quoted argument'}}
$arguments=($values|ForEach-Object {'"'+$_+'"'}) -join ' '
$process=Start-Process -FilePath $ssh -ArgumentList $arguments -WindowStyle Hidden -RedirectStandardError $log -PassThru
try{
    $deadline=(Get-Date).AddSeconds(20)
    $ready=$false
    do{
        if($process.HasExited){throw ('SSH tunnel failed; diagnostic: '+$log)}
        try{$health=Invoke-RestMethod -Uri "http://127.0.0.1:$LocalPort/health" -TimeoutSec 1;if($health.ok -eq $true){$ready=$true;break}}catch{}
        Start-Sleep -Milliseconds 250
    }while((Get-Date) -lt $deadline)
    if(!$ready){throw 'Application readiness timeout'}
    Write-Output "PASS: private panel http://127.0.0.1:$LocalPort via encrypted SSH to $VMHost"
    Write-Output 'Keep this window open. Ctrl+C ends this temporary access. No startup entry was installed.'
    if(!$NoBrowser){Start-Process "http://127.0.0.1:$LocalPort/"}
    Wait-Process -Id $process.Id
}finally{
    if(!$process.HasExited){Stop-Process -Id $process.Id -ErrorAction SilentlyContinue}
}
