# 打包源码 zip（排除 .venv / data / 编译产物 / 日志）
param(
  [string]$Out = ""
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $Out) {
  $Out = Join-Path $root ("dist\UtingSource-" + (Get-Date -Format 'yyyyMMdd') + ".zip")
}

$stage = Join-Path $env:TEMP ("uting_src_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
$pkg = Join-Path $stage 'UtingSource'
New-Item -ItemType Directory -Path $pkg -Force | Out-Null

# 需要打包的目录
$dirs = @('app', 'uting', 'tools', 'docs', 'ios-app', 'protector')
foreach ($d in $dirs) {
  $src = Join-Path $root $d
  if (-not (Test-Path $src)) { continue }
  Copy-Item $src (Join-Path $pkg $d) -Recurse -Force
}

# 需要打包的文件
$files = @(
  'README.md', 'README_UTING.md', 'README_IOS.md', 'SEND_TO_OTHERS.md',
  'requirements.txt', '.env.example', 'run.py', 'pack_entry.py',
  'launcher.cs', 'start.cmd', 'stop.cmd', 'app.ico'
)
foreach ($f in $files) {
  $src = Join-Path $root $f
  if (Test-Path $src) { Copy-Item $src (Join-Path $pkg $f) -Force }
}

# 清理不该带的东西
Get-ChildItem $pkg -Recurse -Directory -Include '__pycache__', '.pytest_cache', 'node_modules', 'data' -ErrorAction SilentlyContinue |
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem $pkg -Recurse -File -Include '*.pyc', '*.pyo', '*.log', '*.db', '*.db-wal', '*.db-shm', 'secret.key', '.env', 'llm.json' -ErrorAction SilentlyContinue |
  Remove-Item -Force -ErrorAction SilentlyContinue

# 生成清单，便于对方核对
$all = Get-ChildItem $pkg -Recurse -File
$manifest = $all | ForEach-Object {
  $rel = $_.FullName.Substring($pkg.Length + 1)
  "{0}`t{1}" -f $rel, $_.Length
}
$manifest | Set-Content (Join-Path $pkg 'FILE_LIST.txt') -Encoding UTF8

New-Item -ItemType Directory -Path (Split-Path $Out) -Force | Out-Null
if (Test-Path $Out) { Remove-Item $Out -Force }
Compress-Archive -Path (Join-Path $pkg '*') -DestinationPath $Out -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force

$size = [math]::Round((Get-Item $Out).Length / 1KB, 1)
$count = $all.Count
Write-Host ""
Write-Host ("已生成：{0}" -f $Out) -ForegroundColor Green
Write-Host ("  文件数：{0} 个    大小：{1} KB" -f $count, $size)
Write-Host "  已排除：.venv / data(账号库、密钥) / __pycache__ / 编译产物 / 日志"
