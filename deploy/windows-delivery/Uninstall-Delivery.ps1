param([string]$BaseDirectory='C:\ProgramData\TracerTransfer-Delivery',[switch]$ValidateOnly)
$ErrorActionPreference='Stop'
$id='TracerTransferDelivery'
$p=[IO.Path]::GetFullPath($BaseDirectory)
while($p){if(Test-Path -LiteralPath $p){if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Links/junctions forbidden'}};$p=[IO.Path]::GetDirectoryName($p)}
$wrapper="$BaseDirectory\$id.exe"
if((Get-FileHash $wrapper -Algorithm SHA256).Hash -ne '923111C7142B3DC783A3C722B19B8A21BCB78222D7A136AC33F0CA8A29F4CB66'){throw 'WinSW pinned hash mismatch'}
$service=Get-CimInstance Win32_Service -Filter "Name='$id'"
if($service -and ![String]::Equals([IO.Path]::GetFullPath($service.PathName.Trim('"')),[IO.Path]::GetFullPath($wrapper),[StringComparison]::OrdinalIgnoreCase)){throw 'Service path mismatch'}
$state=Get-Content "$BaseDirectory\install-state.json" -Raw | ConvertFrom-Json
if($state.ServiceSid -notmatch '^S-1-5-80-(\d+-){4}\d+$'){throw 'Invalid recorded service SID'}
$p=[IO.Path]::GetFullPath($state.OutputDirectory)
while($p){if(Test-Path -LiteralPath $p){if((Get-Item -LiteralPath $p -Force).Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'Output links/junctions forbidden'}};$p=[IO.Path]::GetDirectoryName($p)}
if($ValidateOnly){Write-Output 'PASS: uninstall validated without changes; data/output will be preserved';exit 0}
if(!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator token required'}
if($service){
    if($service.State -ne 'Stopped'){Stop-Service $id}
    (Get-Service $id).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(60))
    & $wrapper uninstall
    if($LASTEXITCODE -ne 0){throw 'WinSW uninstall failed'}
}
if(Get-Service $id -ErrorAction SilentlyContinue){throw 'Service still registered'}
# Remove only our SID; do not restore an old full ACL over later admin changes.
$acl=Get-Acl -LiteralPath $state.OutputDirectory
foreach($rule in @($acl.Access)){
    if(!$rule.IsInherited -and $rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $state.ServiceSid){
        $acl.RemoveAccessRuleSpecific($rule)
    }
}
Set-Acl -LiteralPath $state.OutputDirectory -AclObject $acl
$remaining=@((Get-Acl -LiteralPath $state.OutputDirectory).Access|Where-Object {!$_.IsInherited -and $_.AccessControlType -eq 'Allow' -and $_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -eq $state.ServiceSid})
if($remaining.Count){throw 'Service removed but output SID cleanup needs inspection'}
Write-Output 'PASS: service removed. Output, original files, DPAPI identity and journal preserved; revoke device centrally if retiring. No data purge performed.'
