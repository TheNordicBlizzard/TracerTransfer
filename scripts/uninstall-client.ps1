param([Parameter(Mandatory=$true)][string]$ServerUrl)
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'task-common.ps1')
Assert-TracerAdministrator -ServerUrl $ServerUrl
Unregister-ScheduledTask -TaskName 'TracerTransfer-Cliente' -Confirm:$false
if (Get-ScheduledTask -TaskName 'TracerTransfer-Cliente' -ErrorAction SilentlyContinue) { throw 'Remocao nao confirmada.' }
Write-Output 'Inicializacao removida; dados e arquivos preservados.'
