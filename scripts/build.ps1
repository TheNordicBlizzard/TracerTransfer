[CmdletBinding()]
param([string]$Version = '0.1.3', [switch]$LegacyServer)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Versao X.Y.Z obrigatoria.' }
Push-Location $root
try {
    foreach ($component in @('client', 'delivery')) {
        & uv run --frozen python -m PyInstaller --noconfirm --clean --distpath "dist/$Version" "packaging/$component.spec"
        if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar $component" }
    }
    # Legacy diagnostic executable only; never install/package the Windows server.
    if ($LegacyServer) {
        & uv run --frozen python -m PyInstaller --noconfirm --clean --distpath "dist/legacy/$Version" packaging/server.spec
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar servidor legado' }
    }
    Write-Output "Executaveis gerados em $root/dist/$Version"
} finally { Pop-Location }
