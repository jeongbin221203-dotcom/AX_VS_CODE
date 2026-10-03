# 포트폴리오 서버를 이 PC에서 켠다 (Windows 로그인 때 + 10분마다 작업 스케줄러가 실행, 여러 번 실행해도 안전).
#   1) 자재관리 앱(waitress, 127.0.0.1:5920) — 데이터는 저장소 밖 %USERPROFILE%\mm-portfolio 에 저장(재시작해도 남음)
#   2) Cloudflare 임시 터널(https://*.trycloudflare.com) — PC를 인터넷에 직접 열지 않고 공개 주소를 받는다
#   3) 터널 주소가 바뀌었으면 Render 고정 주소(MM_FORWARD_URL)를 새 주소로 바꾸고 다시 배포
#      → 방문자는 늘 같은 Render 주소로 들어오고, PC가 켜져 있으면 PC로, 꺼져 있으면 Render 임시 시연으로 간다.
#   Render API 키는 %USERPROFILE%\mm-portfolio\render.key 에 한 줄로 둔다(없으면 3번만 건너뜀).
#   기록: mm-portfolio\app.log · app.err.log · tunnel.log · start.log
$ErrorActionPreference = "Stop"
$app = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$root = Join-Path $env:USERPROFILE "mm-portfolio"
$port = 5920
$renderService = "srv-davsejugekts73f9stlg"
New-Item -ItemType Directory -Force $root | Out-Null
$log = Join-Path $root "start.log"
function Note($msg) { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Out-File -Append -Encoding utf8 $log; Write-Output $msg }

$python = "$env:USERPROFILE\miniforge3\python.exe"
if (-not (Test-Path $python)) { $python = (Get-Command python).Source }
$cloudflared = "${env:ProgramFiles(x86)}\cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflared)) { $cloudflared = (Get-Command cloudflared).Source }

# 세션 키는 처음 한 번 만들어 두고 계속 쓴다 (PC를 다시 켜도 방문자가 로그아웃되지 않게)
$secretFile = Join-Path $root "secret.txt"
if (-not (Test-Path $secretFile)) {
    -join ((1..32) | ForEach-Object { '{0:x2}' -f (Get-Random -Maximum 256) }) | Out-File -Encoding ascii $secretFile
}
$env:MM_SECRET_KEY = (Get-Content $secretFile -Raw).Trim()
$env:MM_DB_PATH = Join-Path $root "materials.db"
$env:MM_DEMO = "1"
$env:MM_COOKIE_SECURE = "1"
$env:MM_TRUST_PROXY = "1"
$env:MM_IDLE_MINUTES = "720"
$env:MM_PORT = "$port"
$env:PYTHONIOENCODING = "utf-8"

# 1) 앱
if (-not (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)) {
    if (Test-Path $env:MM_DB_PATH) {
        # 켤 때마다 백업 한 벌 (mm-portfolio\backups, 오래된 것은 앱 설정대로 정리)
        & $python -m flask --app app backup 2>&1 | Out-Null
    }
    Start-Process -FilePath $python -WorkingDirectory $app -WindowStyle Hidden `
        -ArgumentList "-m", "waitress", "--listen=127.0.0.1:$port", "--threads=8", "--call", "app:create_app" `
        -RedirectStandardOutput (Join-Path $root "app.log") -RedirectStandardError (Join-Path $root "app.err.log")
    Note "앱 켬 (포트 $port)"
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 1
        if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { break }
    }
}

# 2) 터널 (이미 떠 있으면 그 주소를 그대로 쓴다)
$tunnelLog = Join-Path $root "tunnel.log"
$running = Get-CimInstance Win32_Process -Filter "Name='cloudflared.exe'" |
    Where-Object { $_.CommandLine -like "*127.0.0.1:$port*" }
if (-not $running) {
    if (Test-Path $tunnelLog) { Remove-Item $tunnelLog -Force }
    Start-Process -FilePath $cloudflared -WindowStyle Hidden `
        -ArgumentList "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:$port", "--logfile", "`"$tunnelLog`""
    Note "터널 켬"
}
$url = $null
for ($i = 0; $i -lt 60 -and -not $url; $i++) {
    if (Test-Path $tunnelLog) {
        $m = Select-String -Path $tunnelLog -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -AllMatches |
            Select-Object -Last 1
        if ($m) { $url = $m.Matches[-1].Value }
    }
    if (-not $url) { Start-Sleep -Seconds 1 }
}
if (-not $url) { Note "터널 주소를 찾지 못함 — tunnel.log 확인"; exit 1 }
$url | Out-File -Encoding ascii (Join-Path $root "current_url.txt")

# 3) Render 고정 주소가 이 PC를 가리키게
$keyFile = Join-Path $root "render.key"
if (-not (Test-Path $keyFile)) { Note "PC 주소: $url (render.key 없음 — Render 연결 건너뜀)"; exit 0 }
$headers = @{ Authorization = "Bearer $((Get-Content $keyFile -Raw).Trim())"; "Content-Type" = "application/json" }
$api = "https://api.render.com/v1/services/$renderService"
$current = ""
try { $current = (Invoke-RestMethod -Headers $headers -Uri "$api/env-vars/MM_FORWARD_URL").value } catch { }
if ($current -ne $url) {
    Invoke-RestMethod -Method Put -Headers $headers -Uri "$api/env-vars/MM_FORWARD_URL" `
        -Body (@{ value = $url } | ConvertTo-Json) | Out-Null
    Invoke-RestMethod -Method Post -Headers $headers -Uri "$api/deploys" `
        -Body (@{ clearCache = "do_not_clear" } | ConvertTo-Json) | Out-Null
    Note "Render 연결 주소 변경 → $url (배포 시작, 2~3분 뒤 반영)"
} else {
    Note "그대로 동작 중: $url"
}
