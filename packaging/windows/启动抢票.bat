@echo off
setlocal
cd /d "%~dp0"
title Damai Ticket PC Console

set "CHROME_PATH=%~dp0browser\chrome-win64\chrome.exe"
set "DAMAI_CHROME_BINARY=%~dp0browser\chrome-win64\chrome.exe"
set "CHROMEDRIVER_PATH=%~dp0browser\chromedriver-win64\chromedriver.exe"
set "SE_CACHE_PATH=%~dp0browser\selenium-cache"
set "DAMAI_CHROME_VERSION="
if exist "%~dp0browser\VERSION" set /p DAMAI_CHROME_VERSION=<"%~dp0browser\VERSION"

echo Starting Damai ticket console...
echo The browser will open automatically.
echo Close this window to stop the service.
echo.

python\python.exe app.py --port 8765 --project-root "%~dp0."
if errorlevel 1 (
  echo.
  echo Failed to start. Make sure the package is fully extracted
  echo and port 8765 is not in use.
  pause
)
endlocal
