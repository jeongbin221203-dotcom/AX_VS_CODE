# 차트분석 앱을 화면 없이 백그라운드로 켠다 (Windows 로그인 때 작업 스케줄러가 실행).
#   이미 켜져 있으면(5006번 포트 사용 중) 아무것도 하지 않는다.
#   앱이 켜져 있어야 평일 16:10 일봉 갱신·신호 훑기·휴대폰 게시가 돌고, 놓친 갱신은 켜지는 즉시 따라잡는다.
#   기록: data\app5006.log, data\app5006.err.log
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = if ($env:STOCK_PORT) { [int]$env:STOCK_PORT } else { 5006 }

if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) {
    Write-Output "이미 켜져 있음 (포트 $port)"
    exit 0
}

$python = "$env:USERPROFILE\miniforge3\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python).Source }

Start-Process -FilePath $python -ArgumentList "app.py" -WorkingDirectory $here -WindowStyle Hidden `
    -RedirectStandardOutput "$here\data\app5006.log" -RedirectStandardError "$here\data\app5006.err.log"
Write-Output "켰음 (포트 $port)"
