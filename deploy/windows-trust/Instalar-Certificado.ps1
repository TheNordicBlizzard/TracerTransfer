[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$CertificatePath,
    [Parameter(Mandatory)] [ValidatePattern('^[0-9A-Fa-f]{40}$')] [string]$ExpectedThumbprint,
    [switch]$Remove
)
# Confia (ou deixa de confiar) no certificado interno de assinatura do TracerTransfer
# NESTE computador. Requer PowerShell como Administrador.
# Seguranca: so aceita um certificado de ENTIDADE FINAL (CA=false) cujo unico uso e
# "Code Signing" e cujo thumbprint confere com o informado pelo administrador.
# Por isso ele nao pode emitir certificados nem ser usado para interceptar HTTPS.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Execute este script em um PowerShell como Administrador.' }
$cert = New-Object Security.Cryptography.X509Certificates.X509Certificate2((Resolve-Path -LiteralPath $CertificatePath).Path)
if ($cert.Thumbprint -ne $ExpectedThumbprint.ToUpperInvariant()) { throw "Thumbprint divergente: $($cert.Thumbprint). Nao instale este arquivo." }
if ($cert.HasPrivateKey) { throw 'O arquivo contem chave privada; distribua somente o .cer publico.' }
$basic = $cert.Extensions | Where-Object { $_ -is [Security.Cryptography.X509Certificates.X509BasicConstraintsExtension] }
if (-not $basic -or $basic.CertificateAuthority) { throw 'Recusado: o certificado nao declara CA=false.' }
$eku = @($cert.Extensions | Where-Object { $_ -is [Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension] } | ForEach-Object { $_.EnhancedKeyUsages } | ForEach-Object { $_.Value })
if ($eku.Count -ne 1 -or $eku[0] -ne '1.3.6.1.5.5.7.3.3') { throw 'Recusado: o certificado deve ter somente o uso Code Signing.' }
foreach ($storeName in @('Root', 'TrustedPublisher')) {
    $store = New-Object Security.Cryptography.X509Certificates.X509Store($storeName, 'LocalMachine')
    $store.Open('ReadWrite')
    try {
        if ($Remove) { $store.Remove($cert); Write-Output "Removido de LocalMachine\$storeName" }
        else { $store.Add($cert); Write-Output "Instalado em LocalMachine\$storeName" }
    } finally { $store.Close() }
}
Write-Output "Certificado: $($cert.Subject) · valido ate $($cert.NotAfter.ToString('yyyy-MM-dd'))"
