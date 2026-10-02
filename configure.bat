@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Настройка Ending

if not exist "venv\Scripts\python.exe" (
  echo Сначала запустите install.bat.
  pause
  exit /b 1
)

"%~dp0venv\Scripts\python.exe" setup_ending.py --configure
echo.
pause
