@echo off
cd /d "%~dp0"
python --version >nul 2>nul
if errorlevel 1 (
  echo.
  echo Python 3 was not found on this computer.
  echo You do not need it for normal use: open the chart HTML file and click
  echo the update button in the top-right corner instead.
  echo.
  pause
  exit /b 1
)
python build.py
echo.
pause
