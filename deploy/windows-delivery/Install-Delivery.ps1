param(
    [string]$BaseDirectory='C:\ProgramData\TracerTransfer-Delivery',
    [string]$OutputDirectory='C:\FreeForm\Tracer',
    [string]$BinaryPath,
    [string]$WrapperPath,
    [string]$ExpectedBinarySha256,
    [string]$BootstrapPath,
    [string[]]$ConsumerReadSids=@(),
    [switch]$ValidateOnly
)
$ErrorActionPreference='Stop'
$id='TracerTransferDelivery'
$expectedWrapper='923111C7142B3DC783A3C722B19B8A21BCB78222D7A136AC33F0CA8A29F4CB66'
if(!$BinaryPath){$BinaryPath=Join-Path $PSScriptRoot 'bin\TracerEntregador.exe'}
if(!$WrapperPath){$WrapperPath=Join-Path $PSScriptRoot 'WinSW.NET4.exe'}
function Native($Exe,[string[]]$Arguments){ & $Exe @Arguments; if($LASTEXITCODE -ne 0){throw "Native prerequisite/action failed: $Exe"} }
function RealPath([string]$Path){
    $p=[IO.Path]::GetFullPath($Path)
    while($p){
        if(Test-Path -LiteralPath $p){$x=Get-Item -LiteralPath $p -Force; if($x.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Links/junctions are forbidden'}}
        $next=[IO.Path]::GetDirectoryName($p); if($next -eq $p){break}; $p=$next
    }
}
function PrivateAcl([string]$Path,[string]$ServiceSid,[string]$ServiceRights){
    $a=New-Object Security.AccessControl.DirectorySecurity
    $a.SetAccessRuleProtection($true,$false)
    foreach($s in @('S-1-5-18','S-1-5-32-544')){
        $a.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($s)),'FullControl','ContainerInherit,ObjectInherit','None','Allow')))
    }
    $a.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($ServiceSid)),$ServiceRights,'ContainerInherit,ObjectInherit','None','Allow')))
    Set-Acl -LiteralPath $Path -AclObject $a
}
foreach($p in @($BaseDirectory,$OutputDirectory,$BinaryPath,$WrapperPath)){RealPath $p}
if(!(Test-Path -LiteralPath $OutputDirectory -PathType Container)){throw 'Output must already exist; inventory FreeForm and ACLs first'}
if([IO.Path]::GetPathRoot([IO.Path]::GetFullPath($BaseDirectory)) -ne [IO.Path]::GetPathRoot([IO.Path]::GetFullPath($OutputDirectory))){throw 'Data/staging and output must share a volume'}
$b=[IO.Path]::GetFullPath($BaseDirectory).TrimEnd('\')
$o=[IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\')
if($b -eq $o -or $b.StartsWith($o+'\',[StringComparison]::OrdinalIgnoreCase) -or $o.StartsWith($b+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Private data and output must be separate trees'}
if(!(Test-Path -LiteralPath $WrapperPath -PathType Leaf) -or (Get-FileHash -LiteralPath $WrapperPath -Algorithm SHA256).Hash -ne $expectedWrapper){throw 'Provide official WinSW.NET4.exe 2.12.0 matching pinned hash; no download performed'}
if($ExpectedBinarySha256 -notmatch '^[0-9a-fA-F]{64}$' -or !(Test-Path -LiteralPath $BinaryPath -PathType Leaf) -or (Get-FileHash -LiteralPath $BinaryPath -Algorithm SHA256).Hash -ne $ExpectedBinarySha256){throw 'Application payload checksum required and must match'}
if(!$ConsumerReadSids.Count){throw 'Explicit inventoried FreeForm consumer SID(s) required: hardlink preserves staging ACL'}
foreach($s in $ConsumerReadSids){$null=New-Object Security.Principal.SecurityIdentifier($s);if($s -in @('S-1-1-0','S-1-5-11','S-1-5-32-545')){throw 'Broad consumer grants forbidden'}}
$template=Join-Path $PSScriptRoot 'TracerTransferDelivery.xml'
[xml]$xml=Get-Content -LiteralPath $template -Raw
if($xml.service.id -ne $id -or $xml.service.serviceaccount.user -ne 'LocalService' -or $xml.service.startmode -ne 'Automatic'){throw 'Unexpected service template'}
if(!$BootstrapPath){throw 'Private activation bootstrap JSON path required; never pass code/password in arguments'}
RealPath $BootstrapPath
$acl=Get-Acl -LiteralPath $BootstrapPath
if(!$acl.AreAccessRulesProtected){throw 'Bootstrap source must have protected DACL (SYSTEM and Administrators only)'}
foreach($r in $acl.Access){if($r.AccessControlType -eq 'Allow' -and $r.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')){throw 'Bootstrap source ACL is not private'}}
$bootstrap=Get-Content -LiteralPath $BootstrapPath -Raw | ConvertFrom-Json
if($bootstrap.code -cnotmatch '^TT-[0-9A-F]{40}$'){throw 'Invalid activation code format'}
if(Get-Service $id -ErrorAction SilentlyContinue){throw 'Service exists; use Update-Delivery.ps1'}
if(Test-Path -LiteralPath $BaseDirectory){throw 'Refuse existing target; inspect previous install/state manually'}
if($ValidateOnly){Write-Output 'PASS: prerequisites validated; no service, process, ACL or files changed';exit 0}
if(!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator token required'}
# WinSW install creates the identity; no runtime starts before SID and ACL setup.
New-Item -ItemType Directory -Path $BaseDirectory | Out-Null
# Restrict the root immediately, before writing a bootstrap or runtime identity.
$rootAcl=New-Object Security.AccessControl.DirectorySecurity
$rootAcl.SetAccessRuleProtection($true,$false)
foreach($s in @('S-1-5-18','S-1-5-32-544')){$rootAcl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule((New-Object Security.Principal.SecurityIdentifier($s)),'FullControl','ContainerInherit,ObjectInherit','None','Allow')))}
Set-Acl -LiteralPath $BaseDirectory -AclObject $rootAcl
New-Item -ItemType Directory -Path "$BaseDirectory\bin","$BaseDirectory\data","$BaseDirectory\data\staging","$BaseDirectory\logs","$BaseDirectory\tmp" | Out-Null
Copy-Item -LiteralPath $BinaryPath -Destination "$BaseDirectory\bin\TracerEntregador.exe"
Copy-Item -LiteralPath $WrapperPath -Destination "$BaseDirectory\$id.exe"
$xml.service.arguments='--data-dir "%BASE%\data" --output-dir "'+$OutputDirectory+'"'
$xml.Save("$BaseDirectory\$id.xml")
Native "$BaseDirectory\$id.exe" @('install')
Native "$env:WINDIR\System32\sc.exe" @('sidtype',$id,'unrestricted')
$sid=(New-Object Security.Principal.NTAccount('NT SERVICE',$id)).Translate([Security.Principal.SecurityIdentifier]).Value
PrivateAcl $BaseDirectory $sid 'ReadAndExecute'
foreach($p in @('data','logs','tmp')){PrivateAcl (Join-Path $BaseDirectory $p) $sid 'Modify'}
PrivateAcl "$BaseDirectory\data\staging" $sid 'Modify'
foreach($s in $ConsumerReadSids){Native "$env:WINDIR\System32\icacls.exe" @("$BaseDirectory\data\staging",'/grant',"*${s}:(OI)(IO)R")}
# Preserve output ACL and all existing files; add only service SID create/read access.
(Get-Acl -LiteralPath $OutputDirectory).Sddl | Set-Content -LiteralPath "$BaseDirectory\output-before.sddl" -Encoding ASCII
Native "$env:WINDIR\System32\icacls.exe" @($OutputDirectory,'/grant',"*${sid}:(RX,WD)")
# Bootstrap file is private even though normal data files inherit permissions.
$destination="$BaseDirectory\data\bootstrap.json"
# Consume the supplied provisioning artifact instead of retaining plaintext copies.
Move-Item -LiteralPath $BootstrapPath -Destination $destination
Native "$env:WINDIR\System32\icacls.exe" @($destination,'/inheritance:r','/grant:r','*S-1-5-18:F','*S-1-5-32-544:F',"*${sid}:M")
@{OutputDirectory=$OutputDirectory;ServiceSid=$sid;ConsumerReadSids=$ConsumerReadSids}|ConvertTo-Json|Set-Content -LiteralPath "$BaseDirectory\install-state.json" -Encoding UTF8
Start-Service $id
# SCM Running alone is not activation/readiness. Wait for real successful polling.
$deadline=(Get-Date).AddSeconds(90)
do{
    $statusPath="$BaseDirectory\data\status.json"
    if(Test-Path $statusPath){try{$status=Get-Content $statusPath -Raw|ConvertFrom-Json;if($status.status -eq 'poll_ok' -and !(Test-Path $destination)){Write-Output 'PASS: service activated and polling; bootstrap consumed (no secure-erasure claim)';exit 0}}catch{}}
    Start-Sleep -Seconds 1
}while((Get-Date) -lt $deadline)
throw 'Service installed but activation/poll readiness failed; preserve journal and inspect status.json; never run activation as administrator'
