param([Parameter(Mandatory=$true)][string]$ServerUrl,[string]$ExePath=(Join-Path $PSScriptRoot '../dist/TracerCliente.exe'),[string]$DataDir=(Join-Path $env:LOCALAPPDATA 'TracerTransfer/client'))
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'task-common.ps1')
Install-TracerTask -Name 'TracerTransfer-Cliente' -ExePath $ExePath -DataDir $DataDir -ServerUrl $ServerUrl -Background
