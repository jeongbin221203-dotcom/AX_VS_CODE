@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
echo Open http://127.0.0.1:5091 in your browser. Stop with Ctrl+C.
%PY% -m http.server 5091 --bind 127.0.0.1
pause
