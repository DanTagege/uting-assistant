# 端到端冒烟测试：登录 -> 总览 -> 投递答题 -> 轮询 -> 试卷回显 -> 管理端
# 用法：powershell -ExecutionPolicy Bypass -File tools\smoke_test.ps1
$ErrorActionPreference = 'Stop'
$base = 'http://127.0.0.1:8710'
$dir = Join-Path $PSScriptRoot '..\data'
$jar = Join-Path $dir 'cookies.txt'
$body = Join-Path $dir 'login.json'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

function J($obj) { $obj | ConvertTo-Json -Compress -Depth 6 }

'{ "username": "13800000001", "password": "demo1234", "challenge": "local", "validate": "", "seccode": "" }' |
  Out-File -Encoding utf8 -NoNewline $body

$h = @(
  '-H', 'Content-Type: application/json',
  '-H', 'X-UCampus-Request: 1',
  '-H', 'X-UCampus-Fingerprint: 5:smoketestvisitor',
  '-H', 'X-UCampus-Device-Info: %7B%22platform%22%3A%22Win32%22%2C%22timezone%22%3A%22Asia%2FShanghai%22%2C%22screen%22%3A%221920x1080%22%7D'
)

Write-Host "== 1. 登录 ==" -ForegroundColor Cyan
$login = curl.exe -sS -c $jar -X POST "$base/api/login" @h --data-binary "@$body" | ConvertFrom-Json
$login | ConvertTo-Json -Depth 4 | Write-Host
$sid = $login.sessionId
if (-not $sid) { throw '登录失败' }
$h += @('-H', "X-UCampus-Session: $sid")

Write-Host "`n== 2. 会话校验 ==" -ForegroundColor Cyan
curl.exe -sS -b $jar "$base/api/auth/session" | Write-Host

Write-Host "`n== 3. 平台统计 ==" -ForegroundColor Cyan
curl.exe -sS "$base/api/stats" | Write-Host
curl.exe -sS "$base/api/uptime" | Write-Host

Write-Host "`n== 4. 训练总览 ==" -ForegroundColor Cyan
$ov = curl.exe -sS -b $jar "$base/api/training/overview" | ConvertFrom-Json
"本周 {0}/{1}，待完成 {2}" -f $ov.adaptive.week.done, $ov.adaptive.week.quota, $ov.adaptive.week.pending | Write-Host
$task = $ov.adaptive.tasks | Where-Object { -not $_.locked -and -not $_.completed } | Select-Object -First 1
if (-not $task) { throw '没有可作答任务' }
"选题: {0} ({1}) ansVersion={2}" -f $task.name, $task.taskId, $task.ansVersion | Write-Host

Write-Host "`n== 5. 投递自动答题 ==" -ForegroundColor Cyan
$completeBody = Join-Path $dir 'complete.json'
J @{ ansVersion = $task.ansVersion; questionDelaySeconds = 0 } | Out-File -Encoding utf8 -NoNewline $completeBody
$job = curl.exe -sS -b $jar -X POST "$base/api/training/tasks/$($task.taskId)/complete" @h --data-binary "@$completeBody" | ConvertFrom-Json
$job | ConvertTo-Json -Depth 4 | Write-Host

Write-Host "`n== 6. 轮询任务状态 ==" -ForegroundColor Cyan
$jobId = $job.jobId
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Milliseconds 400
  $j = curl.exe -sS -b $jar "$base/api/training/jobs/$jobId" | ConvertFrom-Json
  "[{0}] status={1} score={2} {3}" -f $i, $j.status, $j.score, $j.error | Write-Host
  if ($j.status -in @('succeeded', 'failed')) { break }
}
$j.lines | ForEach-Object { Write-Host "   $_" -ForegroundColor DarkGray }

Write-Host "`n== 7. 试卷/作答回显 ==" -ForegroundColor Cyan
$paper = curl.exe -sS -b $jar "$base/api/training/jobs/$jobId/paper" | ConvertFrom-Json
"paperHash={0} questions={1} graded={2}" -f $paper.paperHash, $paper.questionCount, $paper.gradedCount | Write-Host
$paper.questions | Select-Object -First 4 | ForEach-Object { "  [{0}] {1} -> {2}" -f $_.action, $_.instanceId, $_.answer } | Write-Host

Write-Host "`n== 8. 托管开关 ==" -ForegroundColor Cyan
curl.exe -sS -b $jar -X POST "$base/api/hosting" @h | Write-Host
curl.exe -sS -b $jar "$base/api/hosting" | Write-Host

Write-Host "`n== 9. 管理端（管理员登录） ==" -ForegroundColor Cyan
$adminBody = Join-Path $dir 'admin-login.json'
'{ "username": "13900000000", "password": "admin1234", "challenge": "local", "validate": "", "seccode": "" }' |
  Out-File -Encoding utf8 -NoNewline $adminBody
$ajar = Join-Path $dir 'admin-cookies.txt'
$alogin = curl.exe -sS -c $ajar -X POST "$base/api/login" @h --data-binary "@$adminBody" | ConvertFrom-Json
$ah = @(
  '-H', 'Content-Type: application/json',
  '-H', 'X-UCampus-Request: 1',
  '-H', "X-UCampus-Session: $($alogin.sessionId)"
)
"is_admin={0}" -f $alogin.profile.is_admin | Write-Host
curl.exe -sS -b $ajar "$base/api/admin/scheduler" | Write-Host
$sched = Join-Path $dir 'sched.json'
J @{ enabled = $true; weekday = (Get-Date).DayOfWeek.value__ - 1; hour = 12; minute = 0; parallelism = 4 } |
  Out-File -Encoding utf8 -NoNewline $sched
curl.exe -sS -b $ajar -X PUT "$base/api/admin/scheduler" @ah --data-binary "@$sched" | Write-Host
curl.exe -sS -b $ajar -X POST "$base/api/admin/scheduler/run" @ah | Write-Host
curl.exe -sS -b $ajar "$base/api/admin/hosted-accounts?limit=5" | Write-Host
curl.exe -sS -b $ajar "$base/api/admin/risk/devices?suspicious=false&limit=5" | Write-Host

Write-Host "`n== 完成 ==" -ForegroundColor Green
