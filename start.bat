@echo off
cd /d "%~dp0"
timeout /t 5 /nobreak > nul
if not exist "mark.exe" (
    echo ERROR: mark.exe not found in %cd%
    pause
    exit /b 1
)
start "" "mark.exe"