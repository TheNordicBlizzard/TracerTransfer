param([string]$Root='C:\ProgramData\TracerTransfer-Pilot')
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
if(Test-Path "$Root\bin\TracerServidor.exe"){throw 'Existing pilot binary: inspect before overwriting'}
New-Item -ItemType Directory -Path "$Root\bin" -Force | Out-Null
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $Root /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if($LASTEXITCODE -ne 0){throw 'ACL failed'}
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$cf=Invoke-RestMethod 'https://api.github.com/repos/cloudflare/cloudflared/releases/latest'
$asset=$cf.assets | Where-Object name -eq 'cloudflared-windows-amd64.exe'
if(!$asset){throw 'Official cloudflared asset missing'}
Invoke-WebRequest -UseBasicParsing $asset.browser_download_url -OutFile "$Root\bin\cloudflared.exe"
$caddy=Invoke-RestMethod 'https://api.github.com/repos/caddyserver/caddy/releases/latest'
$zip=$caddy.assets | Where-Object name -match '^caddy_.*_windows_amd64.zip$'
if(!$zip){throw 'Official Caddy asset missing'}
Invoke-WebRequest -UseBasicParsing $zip.browser_download_url -OutFile "$Root\caddy.zip"
Expand-Archive -LiteralPath "$Root\caddy.zip" -DestinationPath "$Root\caddy-download" -Force
Copy-Item "$Root\caddy-download\caddy.exe" "$Root\bin\caddy.exe"
[ordered]@{Cloudflared=$cf.tag_name;Caddy=$caddy.tag_name;Hashes=@(Get-FileHash "$Root\bin\cloudflared.exe","$Root\bin\caddy.exe" -Algorithm SHA256 | Select-Object Path,Hash)} | ConvertTo-Json -Depth 4
