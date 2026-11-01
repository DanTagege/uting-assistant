param([int]$Port = 8710)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$cand = @()
foreach ($cfg in Get-NetIPConfiguration) {
    if (-not $cfg.IPv4DefaultGateway) { continue }
    if ($cfg.NetAdapter.Status -ne 'Up') { continue }
    foreach ($a in $cfg.IPv4Address) {
        if ($a.IPAddress -match '^(127\.|169\.254\.)') { continue }
        $cand += $a.IPAddress
    }
}
$ip = $cand | Select-Object -First 1
if (-not $ip) {
    $ip = (Get-NetIPAddress -AddressFamily IPv4 |
            Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.InterfaceAlias -match 'WLAN|Wi-Fi|Ethernet' } |
            Select-Object -First 1).IPAddress
}
if (-not $ip) { throw 'no lan ip found' }

$url = 'http://' + $ip + ':' + $Port
$cfgPath = Join-Path $root 'ios-app\capacitor.config.json'
$json = [IO.File]::ReadAllText($cfgPath, [Text.Encoding]::UTF8)
$cfg = $json | ConvertFrom-Json
$cfg.server.url = $url
$out = $cfg | ConvertTo-Json -Depth 8
[IO.File]::WriteAllText($cfgPath, $out, (New-Object Text.UTF8Encoding($false)))

Write-Host ('LAN IP   : ' + $ip) -ForegroundColor Green
Write-Host ('server.url -> ' + $url) -ForegroundColor Green
Write-Host ''
Write-Host 'Next:' -ForegroundColor Cyan
Write-Host '  1) run server on LAN:  $env:UC_HOST=''0.0.0.0''; .\.venv\Scripts\python.exe run.py'
Write-Host '  2) iPhone on the same Wi-Fi'
Write-Host ('  3) Safari open ' + $url + '  -> Share -> Add to Home Screen')
Write-Host '  4) native shell: cd ios-app; npm install; npx cap sync ios'
Write-Host ''
Write-Host 'If the phone cannot open it, allow the port as admin:' -ForegroundColor Yellow
Write-Host ('  New-NetFirewallRule -DisplayName ''Uting ' + $Port + ''' -Direction Inbound -LocalPort ' + $Port + ' -Protocol TCP -Action Allow -Profile Private')
