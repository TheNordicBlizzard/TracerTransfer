$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location $root
try {
    & uv run python -m PyInstaller --noconfirm --clean packaging/client.spec
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar cliente' }
    & uv run python -m PyInstaller --noconfirm --clean packaging/server.spec
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar servidor' }
    & uv run python -m PyInstaller --noconfirm --clean packaging/delivery.spec
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar entregador' }
    & uv run python -m PyInstaller --noconfirm --clean packaging/updater.spec
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar atualizador' }
    Write-Output "Executaveis gerados em $root/dist"
} finally { Pop-Location }
