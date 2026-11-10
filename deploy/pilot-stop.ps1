param([string]$Root='C:\ProgramData\TracerTransfer-Pilot')
$ErrorActionPreference='Stop'
$Root=[IO.Path]::GetFullPath($Root)
$state=Join-Path $Root 'processes.json'
if (!(Test-Path -LiteralPath $state)) { Write-Output 'Pilot not running (no state).'; exit 0 }
$items=Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
foreach($item in ($items | Sort-Object Order -Descending)) {
    $p=Get-Process -Id $item.Id -ErrorAction SilentlyContinue
    if (!$p) { continue }
    if ($p.Path -ne $item.Path -or $p.StartTime.ToUniversalTime().Ticks.ToString() -ne $item.StartTicks) { throw "PID identity changed: $($item.Id)" }
    # Verify the parent before terminating its PyInstaller child tree.
    & "$env:WINDIR\System32\taskkill.exe" /PID $p.Id /T /F | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not stop pilot PID $($p.Id)" }
    Wait-Process -Id $p.Id -Timeout 15 -ErrorAction SilentlyContinue
}
$remaining=@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith($Root+'\',[StringComparison]::OrdinalIgnoreCase)})
if($remaining.Count) { throw 'Pilot processes remain; preserve state for inspection.' }
Remove-Item -LiteralPath $state
Write-Output 'PASS: exact pilot process trees stopped; data and logs preserved.'
