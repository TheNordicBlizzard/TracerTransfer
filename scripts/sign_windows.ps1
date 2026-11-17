[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string[]]$Path,
    [Parameter(Mandatory)] [ValidatePattern('^[0-9A-Fa-f]{40}$')] [string]$Thumbprint,
    [string]$TimestampUrl = 'http://timestamp.digicert.com',
    [string]$Description = 'TracerTransfer',
    [string]$SignToolPath
)
# Assina executaveis com Authenticode (SHA-256 + carimbo de tempo RFC 3161).
# A chave privada nunca sai do repositorio de certificados do Windows: o certificado
# e escolhido pelo thumbprint em CurrentUser\My; nenhum PFX ou senha e usado.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Thumbprint = $Thumbprint.ToUpperInvariant()
$cert = Get-Item -LiteralPath "Cert:\CurrentUser\My\$Thumbprint" -ErrorAction SilentlyContinue
if (-not $cert -or -not $cert.HasPrivateKey) { throw "Certificado $Thumbprint com chave privada nao encontrado em CurrentUser\My." }
if ($cert.NotAfter -le (Get-Date)) { throw 'Certificado de assinatura expirado.' }
if (-not ($cert.EnhancedKeyUsageList.ObjectId -contains '1.3.6.1.5.5.7.3.3')) { throw 'Certificado sem EKU Code Signing.' }
if (-not $SignToolPath) {
    $SignToolPath = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" -ErrorAction SilentlyContinue |
        Sort-Object { [version]$_.Directory.Parent.Name } -Descending | Select-Object -First 1 -ExpandProperty FullName
}
if (-not $SignToolPath -or -not (Test-Path -LiteralPath $SignToolPath -PathType Leaf)) { throw 'signtool.exe (Windows SDK) nao encontrado.' }
$tool = Get-AuthenticodeSignature -LiteralPath $SignToolPath
if ($tool.Status -ne 'Valid' -or $tool.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw 'signtool.exe sem assinatura Microsoft valida.' }
foreach ($item in $Path) {
    $file = (Resolve-Path -LiteralPath $item).Path
    & $SignToolPath sign /q /fd SHA256 /sha1 $Thumbprint /s My /tr $TimestampUrl /td SHA256 /d $Description $file
    if ($LASTEXITCODE -ne 0) { throw "signtool falhou ($LASTEXITCODE) em $file" }
    $sig = Get-AuthenticodeSignature -LiteralPath $file
    if ($sig.SignerCertificate.Thumbprint -ne $Thumbprint) { throw "Assinante inesperado em $file" }
    if (-not $sig.TimeStamperCertificate) { throw "Assinatura sem carimbo de tempo em $file" }
    [pscustomobject]@{ File = $file; Status = $sig.Status; Signer = $sig.SignerCertificate.Subject; Timestamp = $sig.TimeStamperCertificate.Subject }
}
