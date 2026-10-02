@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Остановка Ending

rem Останавливает Ending, запущенный в фоне через start-ending-hidden.vbs или автозапуск.
rem Если Ending запущен через start-ending.bat, просто закройте его окно.

set "PIDFILE=data\ending-backend.pid"

if not exist "%PIDFILE%" (
  echo Фоновый Ending не запущен: нет файла %PIDFILE%.
  goto done
)

set /p ENDING_PID=<"%PIDFILE%"

rem PID мог достаться другой программе, если Ending раньше завершили аварийно. Убиваем только pythonw.exe.
tasklist /FI "PID eq %ENDING_PID%" /FI "IMAGENAME eq pythonw.exe" | find "pythonw.exe" >nul
if errorlevel 1 (
  echo Процесс %ENDING_PID% из pid-файла не похож на Ending, ничего не останавливаю.
  echo Возможно, Ending уже остановлен.
  del "%PIDFILE%" >nul 2>nul
  goto done
)

taskkill /PID %ENDING_PID% /T /F >nul 2>nul
if errorlevel 1 (
  echo Не удалось остановить процесс %ENDING_PID%.
) else (
  echo Ending остановлен.
)
del "%PIDFILE%" >nul 2>nul

:done
echo.
pause
