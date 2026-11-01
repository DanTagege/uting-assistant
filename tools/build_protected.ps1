# 把内层 exe 加密后嵌进保护壳，编译成最终成品
# 用法：powershell -ExecutionPolicy Bypass -File tools\build_protected.ps1 -Inner "D:\Ucampus\build_native\UtingNative.exe" -Out "D:\Ucampus\U听说助手-加固版.exe"
param(
  [Parameter(Mandatory = $true)][string]$Inner,
  [Parameter(Mandatory = $true)][string]$Out,
  [string]$Icon = "D:\Ucampus\app.ico"
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$stage = Join-Path $env:TEMP ("uting_prot_" + [guid]::NewGuid().ToString('N').Substring(0, 8))
New-Item -ItemType Directory -Path $stage -Force | Out-Null

if (-not (Test-Path $Inner)) { throw "内层 exe 不存在：$Inner" }

# 1) 机器绑定的 salt（每台机器不同，换机器解不出来）
$salt = New-Object byte[] 16
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($salt)
[IO.File]::WriteAllText((Join-Path $stage 'salt.b64'), [Convert]::ToBase64String($salt))

# 2) 用 MachineGuid 派生密钥，AES-256-CBC 加密内层 exe
$guid = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Cryptography' -Name MachineGuid -ErrorAction SilentlyContinue).MachineGuid
if (-not $guid) { $guid = "$env:COMPUTERNAME|$env:USERNAME" }
$pwdBytes = [Text.Encoding]::UTF8.GetBytes("uting|$guid")
$kdf = New-Object Security.Cryptography.Rfc2898DeriveBytes($pwdBytes, $salt, 4096)
$key = $kdf.GetBytes(32)

$plain = [IO.File]::ReadAllBytes($Inner)
$aes = [Security.Cryptography.Aes]::Create()
$aes.KeySize = 256; $aes.Mode = 'CBC'; $aes.Padding = 'PKCS7'; $aes.Key = $key
$aes.GenerateIV()
$enc = $aes.CreateEncryptor()
$cipher = $enc.TransformFinalBlock($plain, 0, $plain.Length)
[IO.File]::WriteAllBytes((Join-Path $stage 'payload.bin'), ($aes.IV + $cipher))

# 3) 记录明文 SHA-256（运行期校验完整性）
$sha = [Security.Cryptography.SHA256]::Create()
$hash = ([BitConverter]::ToString($sha.ComputeHash($plain)) -replace '-', '').ToLowerInvariant()
[IO.File]::WriteAllText((Join-Path $stage 'hash.txt'), $hash)

# 4) 编译外壳（资源内嵌）
$csc = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$args = @(
  '/nologo', '/target:winexe', '/platform:anycpu', '/optimize+', '/utf8output',
  "/out:$Out",
  "/win32icon:$Icon",
  "/resource:$stage\payload.bin,payload.bin",
  "/resource:$stage\salt.b64,salt.b64",
  "/resource:$stage\hash.txt,hash.txt",
  "/r:System.dll", "/r:System.Core.dll",
  (Join-Path $root 'protector\Program.cs')
)
& $csc @args
if ($LASTEXITCODE -ne 0) { throw "csc 编译失败（exit $LASTEXITCODE）" }

Remove-Item $stage -Recurse -Force
$size = [math]::Round((Get-Item $Out).Length / 1MB, 2)
Write-Host ""
Write-Host "已生成加固版：$Out（$size MB）" -ForegroundColor Green
Write-Host "  · AES-256-CBC 加密载荷，密钥 = PBKDF2(MachineGuid + 随机 salt, 4096)"
Write-Host "  · 运行期校验明文 SHA-256"
Write-Host "  · 反调试 / 反逆向工具 / 反虚拟机"
Write-Host "  · 注意：只在本机能跑（换机器解不出载荷）"
