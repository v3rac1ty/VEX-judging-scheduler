@echo off
rem Builds dist\VEX-Judging-Scheduler.exe (Windows). Needs Python 3.10+ installed.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv || goto :nopython
)

echo Installing build requirements...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements-build.txt || goto :fail

echo Building the .exe...
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean judging-scheduler.spec || goto :fail

echo.
echo Done: dist\VEX-Judging-Scheduler.exe
exit /b 0

:nopython
echo Python was not found. Install Python 3.10 or newer from https://www.python.org/downloads/
exit /b 1

:fail
echo Build failed.
exit /b 1
