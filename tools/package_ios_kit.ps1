# 打一个交付 zip：PWA 资源 + iOS 工程 + 说明
param([string]$Out = "D:\Ucampus\dist\UtingApp-ios-kit.zip")

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $env:TEMP ("uting-kit-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $stage -Force | Out-Null

function Copy-Tree($from, $to) {
    New-Item -ItemType Directory -Path $to -Force | Out-Null
    Copy-Item "$from\*" $to -Recurse -Force -ErrorAction SilentlyContinue
}

# 1) PWA 资源（可直接丢到任意静态托管）
Copy-Tree (Join-Path $root 'app\static') (Join-Path $stage 'pwa')

# 2) iOS 工程
Copy-Tree (Join-Path $root 'ios-app') (Join-Path $stage 'ios-app')
Remove-Item (Join-Path $stage 'ios-app\node_modules') -Recurse -Force -ErrorAction SilentlyContinue

# 3) 说明
Copy-Item (Join-Path $root 'README_IOS.md') $stage -Force
Copy-Item (Join-Path $root 'README_UTING.md') $stage -Force -ErrorAction SilentlyContinue

# 4) 图标单独放一份，方便做商店素材
Copy-Tree (Join-Path $root 'app\static\icons') (Join-Path $stage 'icons')

New-Item -ItemType Directory -Path (Split-Path $Out) -Force | Out-Null
if (Test-Path $Out) { Remove-Item $Out -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $Out -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force

$size = [math]::Round((Get-Item $Out).Length / 1KB, 1)
Write-Host "已生成 $Out（$size KB）" -ForegroundColor Green
