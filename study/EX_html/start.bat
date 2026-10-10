@echo off
cd /d "%~dp0"
echo http://127.0.0.1:5092 에서 엽니다. 끝내려면 이 창을 닫으세요.
start "" http://127.0.0.1:5092/index.html
python -m http.server 5092 --bind 127.0.0.1
