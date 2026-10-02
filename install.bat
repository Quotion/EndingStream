@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Установка Ending

if not exist "setup_ending.py" (
  echo Не найден файл setup_ending.py.
  echo Распакуйте архив целиком: правая кнопка по архиву, "Извлечь все".
  echo Потом запустите install.bat из распакованной папки.
  pause
  exit /b 1
)

rem Ищем Python 3.10 или новее. "py -3" - лаунчер из официального установщика.
rem Заглушка Microsoft Store на проверку не проходит, это нормально.
set "PY="
for %%C in ("py -3" "python" "python3") do (
  if not defined PY (
    %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=%%~C"
  )
)

if not defined PY (
  echo.
  echo Не найден Python 3.10 или новее.
  echo.
  echo Скачайте Python с https://www.python.org/downloads/
  echo В первом окне установщика поставьте галочку "Add python.exe to PATH".
  echo После установки запустите install.bat ещё раз.
  echo.
  pause
  exit /b 1
)

%PY% setup_ending.py %*
set "CODE=%errorlevel%"

if /i "%~1"=="--no-prompt" exit /b %CODE%

echo.
if not "%CODE%"=="0" echo Установка не завершилась. Исправьте ошибку выше и запустите install.bat ещё раз.
pause
exit /b %CODE%
