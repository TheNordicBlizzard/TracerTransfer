param(
    [string]$BaseDirectory='C:\ProgramData\TracerTransfer-Delivery',
    [Parameter(Mandatory=$true)][string]$ExpectedBinarySha256,
    [string]$InstalledVersion,
    [switch]$Enable,
    [switch]$ValidateOnly
)
$ErrorActionPreference='Stop'
$id='TracerTransferDelivery'
$taskName='TracerTransferDelivery-AutoUpdate'
function RealPath([string]$Path){
    $p=[IO.Path]::GetFullPath($Path)
    if($p -match '["\r\n]'){throw 'Unsafe path'}
    while($p){
        if(Test-Path -LiteralPath $p){if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Links/junctions forbidden'}}
        $p=[IO.Path]::GetDirectoryName($p)
    }
}
function CheckProtectedWrites([string]$Path){
    $a=Get-Acl -LiteralPath $Path
    $owner=$a.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if($owner -notin @('S-1-5-18','S-1-5-32-544')){throw 'Privileged target owner must be SYSTEM/Administrators'}
    $mask=[Security.AccessControl.FileSystemRights]::Write -bor [Security.AccessControl.FileSystemRights]::Delete -bor [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor [Security.AccessControl.FileSystemRights]::ChangePermissions -bor [Security.AccessControl.FileSystemRights]::TakeOwnership
    foreach($r in $a.Access){
        $sid=$r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if($r.AccessControlType -eq 'Allow' -and $sid -notin @('S-1-5-18','S-1-5-32-544') -and ($r.FileSystemRights -band $mask)){throw "Unprivileged write on privileged target: $Path"}
    }
}
RealPath $BaseDirectory
$b=[IO.Path]::GetFullPath($BaseDirectory).TrimEnd('\')
# Parent delete-child/ownership must not permit replacing the protected root.
$trustedInstaller=(New-Object Security.Principal.NTAccount('NT SERVICE','TrustedInstaller')).Translate([Security.Principal.SecurityIdentifier]).Value
$ancestor=[IO.Path]::GetDirectoryName($b)
while($ancestor){
    $acl=Get-Acl -LiteralPath $ancestor
    if($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544',$trustedInstaller)){throw 'Untrusted update ancestor owner'}
    foreach($rule in $acl.Access){
        $sid=$rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
        if($rule.AccessControlType -eq 'Allow' -and !($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -and $sid -notin @('S-1-5-18','S-1-5-32-544',$trustedInstaller) -and ($rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -or $rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::ChangePermissions -or $rule.FileSystemRights -band [Security.AccessControl.FileSystemRights]::TakeOwnership)){throw 'Replaceable update ancestor'}
    }
    $ancestor=[IO.Path]::GetDirectoryName($ancestor)
}
$wrapper=Join-Path $b ($id+'.exe')
foreach($p in @($b,"$b\bin","$b\bin\TracerEntregador.exe",$wrapper,"$b\$id.xml")){RealPath $p;CheckProtectedWrites $p}
if((Get-FileHash -LiteralPath $wrapper -Algorithm SHA256).Hash -ne '923111C7142B3DC783A3C722B19B8A21BCB78222D7A136AC33F0CA8A29F4CB66'){throw 'WinSW pinned hash mismatch'}
$s=Get-CimInstance Win32_Service -Filter "Name='$id'"
if(!$s -or ![String]::Equals([IO.Path]::GetFullPath($s.PathName.Trim('"')),$wrapper,[StringComparison]::OrdinalIgnoreCase) -or $s.StartName -notin @('NT AUTHORITY\LocalService','NT AUTHORITY\Local Service')){throw 'Service identity/path mismatch'}
[xml]$config=Get-Content -LiteralPath "$b\$id.xml" -Raw
if($config.service.id -ne $id -or $config.service.executable -cne '%BASE%\bin\TracerEntregador.exe' -or $config.service.serviceaccount.user -ne 'LocalService'){throw 'Unexpected wrapper configuration'}
if(!(Test-Path -LiteralPath "$b\data\identity.json" -PathType Leaf)){throw 'Activated delivery service required'}
$destination=Join-Path $b 'bin\TracerEntregador.exe'
if($ExpectedBinarySha256 -cnotmatch '^[a-fA-F0-9]{64}$' -or (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash -ne $ExpectedBinarySha256){throw 'Explicit independently verified installed delivery SHA256 pin required'}
$private=Join-Path $b 'updater'
RealPath $private
if(Test-Path -LiteralPath $private){
    CheckProtectedWrites $private
    foreach($item in Get-ChildItem -LiteralPath $private -Recurse -Force){RealPath $item.FullName;CheckProtectedWrites $item.FullName}
}

if($InstalledVersion -and $InstalledVersion -cnotmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'){throw 'InstalledVersion must be strict x.y.z'}
$versionFile=Join-Path $private 'installed_version'
if(Test-Path -LiteralPath $versionFile){
    $recordedVersion=[IO.File]::ReadAllText($versionFile).Trim()
    if($recordedVersion -cnotmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'){throw 'Invalid persisted delivery version'}
    if($InstalledVersion -and $InstalledVersion -cne $recordedVersion){throw 'Refuse to overwrite installed version; reconcile the actual binary first'}
}elseif(!$InstalledVersion){throw 'First enable requires -InstalledVersion with the real delivery version; helper version is not a baseline'}
if($ValidateOnly){Write-Output 'PASS: delivery hash, baseline, service identity, wrapper pin and destination ACLs validated; no changes';exit 0}
if(!$Enable){throw 'Explicit -Enable required; scheduling is administrator opt-in'}
if(!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator token required'}
if(!(Test-Path -LiteralPath $private)){New-Item -ItemType Directory -Path $private | Out-Null}
$a=New-Object Security.AccessControl.DirectorySecurity
$a.SetAccessRuleProtection($true,$false)
foreach($sid in @('S-1-5-18','S-1-5-32-544')){
    $a.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($sid)),'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
}
$a.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
Set-Acl -LiteralPath $private -AclObject $a
CheckProtectedWrites $destination
if(!(Test-Path -LiteralPath $versionFile)){
    $versionTemp=Join-Path $private ('installed_version.'+[guid]::NewGuid().ToString('N')+'.tmp')
    [IO.File]::WriteAllText($versionTemp,$InstalledVersion,[Text.Encoding]::ASCII)
    Move-Item -LiteralPath $versionTemp -Destination $versionFile
}
$action=New-ScheduledTaskAction -Execute $destination -Argument ('--update --base-directory "'+$b+'"') -WorkingDirectory $private
$trigger=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Hours 6)
$principal=New-ScheduledTaskPrincipal -UserId 'S-1-5-18' -LogonType ServiceAccount -RunLevel Highest
$settings=New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 15)
$task=New-ScheduledTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings
Register-ScheduledTask -TaskName $taskName -InputObject $task -Force | Out-Null
$scheduler=New-Object -ComObject Schedule.Service
$scheduler.Connect()
$taskObject=$scheduler.GetFolder('\').GetTask($taskName)
$serviceSid=(New-Object Security.Principal.NTAccount('NT SERVICE',$id)).Translate([Security.Principal.SecurityIdentifier]).Value
# The service may read/trigger, never edit the privileged action.
$taskObject.SetSecurityDescriptor(('O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGX;;;'+$serviceSid+')'),0)
if($taskObject.GetSecurityDescriptor(7) -notmatch [regex]::Escape($serviceSid)){throw 'Task DACL readback mismatch'}
$read=Get-ScheduledTask -TaskName $taskName
if($read.Principal.UserId -notin @('SYSTEM','S-1-5-18','NT AUTHORITY\SYSTEM') -or $read.Actions.Execute -ne $destination -or $read.Actions.Arguments -ne $action.Arguments -or $read.Triggers.Repetition.Interval -ne 'PT6H'){throw 'Scheduled task readback mismatch'}
Write-Output 'PASS: SYSTEM updater scheduled every 6h; LocalService stays unprivileged; first run in five minutes'
