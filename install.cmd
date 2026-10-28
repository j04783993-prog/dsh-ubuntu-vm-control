@echo off
REM Install dsh-ubuntu-vm-control into your DeepSeek Harness profile.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.8+ and try again.
    pause
    exit /b 1
  )
  py install.py %*
) else (
  python install.py %*
)

echo.
pause
