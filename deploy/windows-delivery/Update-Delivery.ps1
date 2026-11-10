param([string]$BaseDirectory='C:\ProgramData\TracerTransfer-Delivery',[string]$BinaryPath,[string]$ExpectedBinarySha256,[switch]$ValidateOnly)
$ErrorActionPreference='Stop'
$id='TracerTransferDelivery'
if(!$BinaryPath){throw 'Explicit signed and verified BinaryPath required'}
foreach($p in @($BaseDirectory,$BinaryPath)){
    $p=[IO.Path]::GetFullPath($p)
    while($p){if(Test-Path -LiteralPath $p){if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Links/junctions forbidden'}};$p=[IO.Path]::GetDirectoryName($p)}
}
$wrapper="$BaseDirectory\$id.exe"
if((Get-FileHash $wrapper -Algorithm SHA256).Hash -ne '923111C7142B3DC783A3C722B19B8A21BCB78222D7A136AC33F0CA8A29F4CB66'){throw 'WinSW pinned hash mismatch'}
foreach($p in @("$BaseDirectory\bin","$BaseDirectory\bin\TracerEntregador.exe",$wrapper,"$BaseDirectory\$id.xml")){
    if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Executable/config links forbidden'}
}
if($ExpectedBinarySha256 -notmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $BinaryPath -Algorithm SHA256).Hash -ne $ExpectedBinarySha256){throw 'Expected payload checksum required'}
$service=Get-CimInstance Win32_Service -Filter "Name='$id'"
if(!$service -or ![String]::Equals([IO.Path]::GetFullPath($service.PathName.Trim('"')),[IO.Path]::GetFullPath($wrapper),[StringComparison]::OrdinalIgnoreCase) -or $service.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\Local Service')){throw 'Service identity/path mismatch'}
if($service.State -ne 'Running'){throw 'Refuse to update or start a previously stopped service'}
if(!(Test-Path "$BaseDirectory\data\identity.json")){throw 'Existing activation required'}
if($ValidateOnly){Write-Output 'PASS: update validated without changes';exit 0}
if(!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator token required'}
$exe="$BaseDirectory\bin\TracerEntregador.exe"
# The detached helper verifies the signed envelope before invoking this script.
# A manually supplied checksum alone is NOT release authorization.
function WaitReady([datetime]$Since){
    $deadline=(Get-Date).AddSeconds(90)
    do{
        if((Get-Service $id).Status -eq 'Running' -and (Test-Path "$BaseDirectory\data\status.json")){
            try{
                $s=Get-Content "$BaseDirectory\data\status.json" -Raw | ConvertFrom-Json
                $child=Get-CimInstance Win32_Process -Filter ("ProcessId="+[int]$s.pid)
                $wrapperProcess=Get-CimInstance Win32_Service -Filter "Name='$id'"
                $parent=Get-CimInstance Win32_Process -Filter ("ProcessId="+[int]$child.ParentProcessId)
                $owned=$child.ParentProcessId -eq $wrapperProcess.ProcessId -or ($parent.ExecutablePath -eq $exe -and $parent.ParentProcessId -eq $wrapperProcess.ProcessId -and $parent.CreationDate.ToUniversalTime() -ge $Since.ToUniversalTime())
                if($s.status -eq 'poll_ok' -and ([DateTimeOffset]::Parse($s.at)).UtcDateTime -ge $Since.ToUniversalTime() -and $child -and $child.ExecutablePath -eq $exe -and $owned -and $child.CreationDate.ToUniversalTime() -ge $Since.ToUniversalTime()){return $true}
            }catch{ }
        }
        Start-Sleep -Seconds 1
    }while((Get-Date) -lt $deadline)
    return $false
}
# Each application retains its own executable-only recovery copy.
$tag=[Guid]::NewGuid().ToString('N')
$backup="$exe.previous.$tag"
$incoming="$exe.incoming.$tag"
$haveBackup=$false
$stopConfirmed=$false
try{
    Stop-Service $id
    (Get-Service $id).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(60))
    $stopConfirmed=$true
    Copy-Item -LiteralPath $exe -Destination $backup
    $haveBackup=$true
    Copy-Item -LiteralPath $BinaryPath -Destination $incoming
    if((Get-FileHash $incoming -Algorithm SHA256).Hash -ne $ExpectedBinarySha256){throw 'Copied binary mismatch'}
    Move-Item -LiteralPath $incoming -Destination $exe -Force
    $since=Get-Date
    Start-Service $id
    if(!(WaitReady $since)){throw 'Updated process not ready'}
    Write-Output "PASS: updated binary polling; identity/journal preserved; backup: $backup"
}catch{
    $failure=$_
    if(!$stopConfirmed){throw 'Stop was not confirmed; no binary replacement or automatic restart allowed'}
    Stop-Service $id -ErrorAction SilentlyContinue
    (Get-Service $id).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(60))
    if($haveBackup){Copy-Item -LiteralPath $backup -Destination $exe -Force}
    $since=Get-Date
    Start-Service $id
    if(!(WaitReady $since)){throw "Update failed; binary rollback readiness FAILED; inspect $backup; no SQLite rollback"}
    throw "Update failed; previous executable restored and polling; journal never rolled back. Error: $($failure.Exception.GetType().Name)"
}finally{
    if(Test-Path -LiteralPath $incoming){Remove-Item -LiteralPath $incoming -Force}
}
