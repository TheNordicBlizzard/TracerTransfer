param([Parameter(Mandatory=$true)][string]$ServerUrl)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'task-common.ps1')
Assert-TracerAdministrator -ServerUrl $ServerUrl
Unregister-ScheduledTask -TaskName 'TracerTransfer-Servidor' -Confirm:$false
if (Get-ScheduledTask -TaskName 'TracerTransfer-Servidor' -ErrorAction SilentlyContinue) { throw 'Remocao nao confirmada.' }
Write-Output 'Inicializacao removida; dados e arquivos preservados.'
