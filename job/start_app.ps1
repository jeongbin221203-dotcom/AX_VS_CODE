# 잡핏 앱을 화면 없이 백그라운드로 켠다 (Windows 로그인 때 작업 스케줄러가 실행).
#   이미 켜져 있으면(5004번 포트 사용 중) 아무것도 하지 않는다.
#   PC가 꺼지며 남은 크롤링 잠금(settings.crawl_lock)을 지운 뒤 켠다 — 그래야 바로 수집이 이어진다.
#   기록: data\app.log, data\app.err.log
$ErrorActionPreference = "Stop"
$job = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = if ($env:JOB_PORT) { [int]$env:JOB_PORT } else { 5004 }

if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
    Write-Output "이미 켜져 있음 (포트 $port)"
    exit 0
}

$python = "$env:USERPROFILE\miniforge3\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python).Source }

Set-Location $job
& $python -c "import config; from core import db; db.configure(config.DB_PATH); db.set_setting('crawl_lock', '')"

Start-Process -FilePath $python -ArgumentList "app.py" -WorkingDirectory $job -WindowStyle Hidden `
    -RedirectStandardOutput "$job\data\app.log" -RedirectStandardError "$job\data\app.err.log"
Write-Output "켰음: http://127.0.0.1:$port"
