param([string]$Username='admin',[string]$VMHost='192.0.2.10',[switch]$ValidateOnly)
$ErrorActionPreference='Stop'
if ($VMHost -eq '192.0.2.10' -and (Test-Path (Join-Path $PSScriptRoot '..\..\deployment.local.json'))) {
    try {
        $cfg = Get-Content (Join-Path $PSScriptRoot '..\..\deployment.local.json') -Raw | ConvertFrom-Json
        if ($cfg.admin_ssh_host) { $VMHost = ($cfg.admin_ssh_host -split '@')[-1] }
    } catch {}
}
if($Username -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$' -or $VMHost -notmatch '^[A-Za-z0-9.-]+$'){throw 'Invalid administrator/host'}
$key=Join-Path $HOME '.ssh\id_ed25519_tracer_vm'
$known=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\tracer-vm.known_hosts'))
foreach($file in @($key,$known)){if(!(Test-Path -LiteralPath $file -PathType Leaf)){throw 'Administrative SSH files missing'}}
$ssh=(Get-Command ssh.exe).Source
$base=@('-i',$key,'-o',('UserKnownHostsFile='+$known),'-o','StrictHostKeyChecking=yes','-o','IdentitiesOnly=yes','-o','BatchMode=yes','-o','ConnectTimeout=8',('traceradmin@'+$VMHost))
function Invoke-PrivateSSH([string]$Remote,[string]$Payload){
    $values=$base+@($Remote)
    foreach($value in $values){if($value.Contains('"')){throw 'Invalid argument'}}
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$ssh;$info.Arguments=(($values|ForEach-Object {'"'+$_+'"'}) -join ' ')
    $info.UseShellExecute=$false;$info.CreateNoWindow=$true
    $info.RedirectStandardInput=$true;$info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true

    $process=New-Object Diagnostics.Process;$process.StartInfo=$info
    try{
        [void]$process.Start()
        $out=$process.StandardOutput.ReadToEndAsync();$err=$process.StandardError.ReadToEndAsync()
        if($Payload){
            $bytes=[Text.Encoding]::UTF8.GetBytes($Payload+"`n")
            try{$process.StandardInput.BaseStream.Write($bytes,0,$bytes.Length);$process.StandardInput.BaseStream.Flush()}
            finally{[Array]::Clear($bytes,0,$bytes.Length)}
        }
        $process.StandardInput.Close()
        if(!$process.WaitForExit(30000)){$process.Kill();throw 'Administrative SSH timeout; check account state before retry'}
        if($process.ExitCode -ne 0){throw 'Administrative operation refused; no existing account was overwritten'}
        return ($out.Result|ConvertFrom-Json)
    }finally{$process.Dispose()}
}
$command='sudo -n /opt/tracer-transfer/venv/bin/python /opt/tracer-transfer/manage_admin.py '
$check=Invoke-PrivateSSH ($command+'check --username '+$Username) ''
if(!$check.ready -or !$check.username_available){throw 'Username already exists; choose a new name or change password through authenticated administration'}
if($ValidateOnly){Write-Output 'PASS: verified SSH/root helper and available administrator name; no account changed';exit 0}
$secure=Read-Host 'Nova senha administrativa (12 a 1024 caracteres; nao envie pelo chat)' -AsSecureString
$confirm=Read-Host 'Confirme a senha' -AsSecureString
$a=[IntPtr]::Zero;$b=[IntPtr]::Zero;$plain=$null;$other=$null;$payload=$null
try{
    $a=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure);$plain=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($a)
    $b=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($confirm);$other=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($b)
    if($plain.Length -lt 12 -or $plain.Length -gt 1024 -or $plain -cne $other){throw 'Senhas diferentes ou fora do limite'}
    $payload=@{username=$Username;password=$plain}|ConvertTo-Json -Compress
    $result=Invoke-PrivateSSH ($command+'create') $payload
    if(!$result.created -or $result.username -cne $Username){throw 'Account read-back failed'}
    Write-Output ('Administrador criado: '+$Username+'. Abra Abrir-Painel.cmd e entre com a senha escolhida.')
}finally{
    if($a -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($a)}
    if($b -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b)}
    $plain=$null;$other=$null;$payload=$null;$secure.Dispose();$confirm.Dispose()
}
