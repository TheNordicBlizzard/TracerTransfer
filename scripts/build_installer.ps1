[CmdletBinding()]
param(
    [switch]$PilotUnsigned,
    [string]$IsccPath,
    [string]$ClientExe,
    [string]$ClientProfile,
    [string]$OutputDirectory,
    [string]$SignToolCommand
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if ($PilotUnsigned -and $SignToolCommand) { throw 'Escolha piloto nao assinado OU assinatura, nunca ambos.' }
if (-not $PilotUnsigned -and -not $SignToolCommand) {
    throw 'Sem assinatura configurada: use -PilotUnsigned explicitamente para um piloto NAO ASSINADO.'
}
$versionMatch = [regex]::Match((Get-Content (Join-Path $root 'pyproject.toml') -Raw), '(?m)^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"\s*$')
if (-not $versionMatch.Success) { throw 'Versao numerica X.Y.Z nao encontrada em pyproject.toml.' }
$version = $versionMatch.Groups[1].Value
if (-not $ClientExe) { $ClientExe = Join-Path $root "dist\$version\TracerCliente.exe" }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $root "dist\installers\$version" }
$profile = $null
$profileHashBefore = $null
if ($ClientProfile) {
    $profile = Get-Item -LiteralPath $ClientProfile
    if ($profile.PSIsContainer) { throw 'ClientProfile deve ser arquivo.' }
    $origin = Get-Content -LiteralPath $profile.FullName -Raw | ConvertFrom-Json
    $keys = @($origin.PSObject.Properties.Name)
    $uri = $null
    if ($keys.Count -ne 1 -or $keys[0] -ne 'server_url' -or $origin.server_url -isnot [string] -or
        -not [Uri]::TryCreate($origin.server_url, [UriKind]::Absolute, [ref]$uri) -or
        $uri.Scheme -ne 'https' -or -not $uri.Host -or $uri.UserInfo -or $uri.Query -or $uri.Fragment -or $uri.AbsolutePath -ne '/') {
        throw 'ClientProfile permite somente server_url com origem HTTPS, sem segredos ou administracao.'
    }
    $profileHashBefore = (Get-FileHash -LiteralPath $profile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
}
$client = Get-Item -LiteralPath $ClientExe
if ($client.PSIsContainer -or $client.Name -ne 'TracerCliente.exe') { throw 'Payload deve ser TracerCliente.exe.' }
# Never run build.ps1 or compete with delivery.spec. A stale client is an explicit gate.
$sources = @((Get-Item (Join-Path $root 'launch_client.py')), (Get-Item (Join-Path $root 'packaging\client.spec')), (Get-Item (Join-Path $root 'pyproject.toml')))
foreach ($folder in @('client', 'common', 'updates')) {
    if (Test-Path (Join-Path $root $folder)) {
        $sources += @(Get-ChildItem (Join-Path $root $folder) -Filter '*.py' -Recurse -File)
    }
}
$stale = @($sources | Where-Object { $_.LastWriteTimeUtc -gt $client.LastWriteTimeUtc })
if ($stale.Count) { throw ('Cliente desatualizado. Compile packaging/client.spec isoladamente. Fonte mais recente: ' + ($stale | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1).FullName) }
$receiptPath = [IO.Path]::ChangeExtension($client.FullName, '.build.json')
$buildReceipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
if ($buildReceipt.schema -ne 1 -or $buildReceipt.component -ne 'client' -or
    $buildReceipt.binary_sha256 -ne (Get-FileHash -LiteralPath $client.FullName -Algorithm SHA256).Hash.ToLowerInvariant()) {
    throw 'Recibo de compilacao ausente/invalido. Recompile packaging/client.spec.'
}
foreach ($source in $buildReceipt.sources.PSObject.Properties) {
    $sourcePath = Join-Path $root $source.Name
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf) -or
        (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $source.Value) {
        throw 'Fonte diverge do recibo de compilacao; recompile packaging/client.spec.'
    }
}
$clientSignature = (Get-AuthenticodeSignature -LiteralPath $client.FullName).Status.ToString()
if ($PilotUnsigned -and $clientSignature -ne 'NotSigned') { throw 'Piloto nao assinado exige payload NotSigned.' }
if (-not $PilotUnsigned -and $clientSignature -ne 'Valid') { throw 'Build assinado exige cliente com Authenticode Valid.' }
if (-not $IsccPath) {
    $command = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($command) { $IsccPath = $command.Source }
    else {
        $candidates = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
        )
        $IsccPath = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    }
}
if (-not $IsccPath -or -not (Test-Path -LiteralPath $IsccPath -PathType Leaf)) {
    throw 'ISCC oficial nao encontrado. Informe -IsccPath; nenhuma instalacao/download automatico sera feito.'
}
$compiler = Get-Item -LiteralPath $IsccPath
$compilerSignature = Get-AuthenticodeSignature -LiteralPath $compiler.FullName
if ($compilerSignature.Status -ne 'Valid') { throw 'ISCC exige assinatura Authenticode Valid.' }
if ($compilerSignature.SignerCertificate.Subject -notmatch 'Pyrsys B\.V\.|Jordan Russell') { throw 'Publicador inesperado do ISCC.' }
[void](New-Item -ItemType Directory -Force -Path $OutputDirectory)
$output = (Resolve-Path -LiteralPath $OutputDirectory).Path
$suffix = if ($PilotUnsigned) { 'pilot-unsigned' } else { 'signed' }
$name = "TracerTransfer-Cliente-$version-windows-x64-$suffix"
$artifact = Join-Path $output ($name + '.exe')
$manifest = Join-Path $output ($name + '.json')
# Do not leave an old manifest describing an interrupted rebuild.
if (Test-Path -LiteralPath $manifest) { Remove-Item -LiteralPath $manifest }
$argsIscc = @('/Qp', "/DAppVersion=$version", "/DClientExe=$($client.FullName)", "/DBuildOutput=$output", "/DArtifactName=$name")
if ($profile) { $argsIscc += "/DClientProfile=$($profile.FullName)" }
if ($PilotUnsigned) { $argsIscc += '/DPilotUnsigned=1' }
else { $argsIscc += @('/DSignToolName=TracerSign', "/STracerSign=$SignToolCommand") }
$argsIscc += (Join-Path $root 'packaging\client.iss')
$clientHashBefore = (Get-FileHash -LiteralPath $client.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
& $compiler.FullName @argsIscc
if ($LASTEXITCODE -ne 0) { throw "ISCC falhou: $LASTEXITCODE" }
if (-not (Test-Path -LiteralPath $artifact -PathType Leaf)) { throw 'ISCC nao produziu o artefato esperado.' }
$clientHashAfter = (Get-FileHash -LiteralPath $client.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
if ($clientHashBefore -ne $clientHashAfter) { throw 'Payload mudou durante o build; artefato nao validado.' }
if ($profile -and $profileHashBefore -ne (Get-FileHash -LiteralPath $profile.FullName -Algorithm SHA256).Hash.ToLowerInvariant()) { throw 'Perfil mudou durante o build.' }
$installerSignature = (Get-AuthenticodeSignature -LiteralPath $artifact).Status.ToString()
if ($PilotUnsigned -and $installerSignature -ne 'NotSigned') { throw 'Artefato piloto deveria ser NotSigned.' }
if (-not $PilotUnsigned -and $installerSignature -ne 'Valid') { throw 'Authenticode do instalador nao e Valid.' }
$record = [ordered]@{
    schema = 1
    version = $version
    channel = $suffix
    built_at_utc = [DateTime]::UtcNow.ToString('o')
    installer = [ordered]@{ path = $artifact; sha256 = (Get-FileHash -LiteralPath $artifact -Algorithm SHA256).Hash.ToLowerInvariant(); size = (Get-Item -LiteralPath $artifact).Length; authenticode = $installerSignature }
    client = [ordered]@{ path = $client.FullName; sha256 = $clientHashAfter; size = $client.Length; authenticode = $clientSignature }
    compiler = [ordered]@{ path = $compiler.FullName; sha256 = (Get-FileHash -LiteralPath $compiler.FullName -Algorithm SHA256).Hash.ToLowerInvariant(); version = $compiler.VersionInfo.FileVersion; authenticode = $compilerSignature.Status.ToString() }
    recipe_sha256 = (Get-FileHash -LiteralPath (Join-Path $root 'packaging\client.iss') -Algorithm SHA256).Hash.ToLowerInvariant()
    clean_windows_validated = $false
    gui_validated = $false
    broad_distribution_approved = $false
}
if ($profile) { $record['profile'] = [ordered]@{ path = $profile.FullName; sha256 = $profileHashBefore; size = $profile.Length } }
$record['build'] = $buildReceipt
[IO.File]::WriteAllText($manifest, ($record | ConvertTo-Json -Depth 5), (New-Object Text.UTF8Encoding($false)))
Write-Output "Artefato: $artifact"
Write-Output "Manifesto: $manifest"
Write-Output "SHA256: $($record.installer.sha256)"
Write-Output "Authenticode: $installerSignature; Windows limpo/GUI/distribuicao ampla permanecem pendentes."
