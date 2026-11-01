# 用 PAT 推送当前仓库到 GitHub（token 只存在于本次进程内存，不落盘、不进 git config）
# 用法：powershell -ExecutionPolicy Bypass -File tools\push_github.ps1
#       （会提示粘贴 token，输入时不回显）
param(
  [string]$User = "DanTagege",
  [string]$Repo = "uting-assistant",
  [string]$Branch = "main"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "仓库：https://github.com/$User/$Repo   分支：$Branch" -ForegroundColor Cyan
Write-Host "请粘贴 Personal Access Token（classic，勾 repo 权限；输入不回显）：" -ForegroundColor Yellow

$secure = Read-Host -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try {
  $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
}

if (-not $token) { throw "token 为空" }

# 用临时 askpass 脚本传凭据，避免 token 出现在命令行/远端 URL
$askpass = Join-Path $env:TEMP ("git_askpass_" + [guid]::NewGuid().ToString('N').Substring(0, 6) + ".cmd")
@"
@echo off
echo %GIT_PUSH_TOKEN%
"@ | Set-Content -Path $askpass -Encoding ASCII

$oldAskpass = $env:GIT_ASKPASS
$oldPrompt = $env:GIT_TERMINAL_PROMPT
$env:GIT_ASKPASS = $askpass
$env:GIT_TERMINAL_PROMPT = "0"
$env:GIT_PUSH_TOKEN = $token

try {
  git remote remove origin 2>$null
  git remote add origin "https://github.com/$User/$Repo.git"
  git push -u origin $Branch
  if ($LASTEXITCODE -ne 0) { throw "git push 失败（exit $LASTEXITCODE）" }
  Write-Host ""
  Write-Host "已推送：https://github.com/$User/$Repo" -ForegroundColor Green
} finally {
  Remove-Item Env:\GIT_PUSH_TOKEN -ErrorAction SilentlyContinue
  $env:GIT_ASKPASS = $oldAskpass
  $env:GIT_TERMINAL_PROMPT = $oldPrompt
  Remove-Item $askpass -Force -ErrorAction SilentlyContinue
  $token = $null
}
