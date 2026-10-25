@echo off
rem Launches Drive Cleaner without a console window.
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw "%~dp0app.py"
    exit /b
)

py -3 -c "import tkinter" >nul 2>nul
if %errorlevel%==0 (
    start "" pyw -3 "%~dp0app.py"
    exit /b
)

echo Python 3 with tkinter is required to run Drive Cleaner.
echo Install it from https://www.python.org/downloads/windows/
pause
