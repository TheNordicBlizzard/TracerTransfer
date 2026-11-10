param([Parameter(Mandatory=$true)][string]$ServerUrl,[string]$ExePath=(Join-Path $PSScriptRoot '../dist/TracerServidor.exe'),[string]$DataDir=(Join-Path $env:LOCALAPPDATA 'TracerTransfer/server'))
$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'task-common.ps1')
Install-TracerTask -Name 'TracerTransfer-Servidor' -ExePath $ExePath -DataDir $DataDir -ServerUrl $ServerUrl -Headless
