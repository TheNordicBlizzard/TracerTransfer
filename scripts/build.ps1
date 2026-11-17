[CmdletBinding()]
param([string]$Version = '0.1.5', [switch]$LegacyServer, [string]$SignThumbprint)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if ($Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Versao X.Y.Z obrigatoria.' }
if ($SignThumbprint -and $SignThumbprint -notmatch '^[0-9A-Fa-f]{40}$') { throw 'SignThumbprint deve ter 40 hexadecimais.' }
Push-Location $root
$previous = $env:TRACER_SIGN_THUMBPRINT
try {
    # Assinatura Authenticode opcional: packaging/build_metadata.py assina cada .exe
    # ANTES de gravar o recibo, que passa a registrar o hash ja assinado.
    $env:TRACER_SIGN_THUMBPRINT = if ($SignThumbprint) { $SignThumbprint.ToUpperInvariant() } else { '' }
    foreach ($component in @('client', 'delivery')) {
        & uv run --frozen python -m PyInstaller --noconfirm --clean --distpath "dist/$Version" "packaging/$component.spec"
        if ($LASTEXITCODE -ne 0) { throw "Falha ao compilar $component" }
    }
    # Legacy diagnostic executable only; never install/package the Windows server.
    if ($LegacyServer) {
        $env:TRACER_SIGN_THUMBPRINT = ''
        & uv run --frozen python -m PyInstaller --noconfirm --clean --distpath "dist/legacy/$Version" packaging/server.spec
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar servidor legado' }
    }
    Write-Output "Executaveis gerados em $root/dist/$Version"
} finally { $env:TRACER_SIGN_THUMBPRINT = $previous; Pop-Location }
