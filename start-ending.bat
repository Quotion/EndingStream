@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Ending

if not exist "venv\Scripts\python.exe" (
  echo Не найдено окружение venv. Сначала запустите install.bat.
  pause
  exit /b 1
)

echo Ending: http://127.0.0.1:8080/ending.html
echo Демо:   http://127.0.0.1:8080/ending.html?demo=1
echo Чтобы остановить, закройте это окно.
echo.

"%~dp0venv\Scripts\python.exe" -m uvicorn backend.app:create_app --factory --host 127.0.0.1 --port 8080
if errorlevel 1 pause
