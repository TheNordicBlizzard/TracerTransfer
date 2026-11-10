function Assert-TracerAdministrator {
    param([Parameter(Mandatory=$true)][string]$ServerUrl)
    if (-not $ServerUrl.StartsWith('https://')) { throw 'HTTPS obrigatorio.' }
    $credential = Get-Credential -Message 'Administrador central do TracerTransfer'
    if ($null -eq $credential) { throw 'Operacao cancelada.' }
    $body = @{ username=$credential.UserName; password=$credential.GetNetworkCredential().Password } | ConvertTo-Json -Compress
    try {
        $login = Invoke-RestMethod -Method Post -Uri ($ServerUrl.TrimEnd('/') + '/api/login') -ContentType 'application/json' -Body $body
        $me = Invoke-RestMethod -Method Get -Uri ($ServerUrl.TrimEnd('/') + '/api/me') -Headers @{ Authorization=('Bearer ' + $login.token) }
        if ($me.role -ne 'admin') { throw 'Permissao de administrador necessaria.' }
    } finally { $body=$null; $credential=$null; $login=$null }
}
function Install-TracerTask {
    param([string]$Name,[string]$ExePath,[string]$DataDir,[string]$ServerUrl,[switch]$Headless,[switch]$Background)
    Assert-TracerAdministrator -ServerUrl $ServerUrl
    $exe = (Resolve-Path -LiteralPath $ExePath).Path
    if ($DataDir.Contains('"') -or $DataDir.Contains("`n") -or $DataDir.Contains("`r")) { throw 'Caminho invalido.' }
    $arguments = '--data-dir "' + $DataDir + '"'
    if ($Headless) { $arguments += ' --headless' }
    if ($Background) { $arguments += ' --background' }
    $action = New-ScheduledTaskAction -Execute $exe -Argument $arguments -WorkingDirectory (Split-Path $exe -Parent)
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
    $principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    $task = Get-ScheduledTask -TaskName $Name
    if ($task.Actions.Execute -ne $exe) { throw 'Tarefa nao confirmada.' }
    Write-Output "Configurada: $Name. Inicializa no proximo login; nao executada agora."
}
