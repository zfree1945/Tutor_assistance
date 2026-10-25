@echo off
setlocal
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
chcp 65001 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto local_python
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 goto python_launcher
python -c "import sys; sys.exit(sys.version_info[:2] != (3, 12))" >nul 2>&1
if errorlevel 1 goto missing_python
python "scripts\start_app.py" %*
goto finished
:python_launcher
py -3.12 "scripts\start_app.py" %*
goto finished
:local_python
".venv\Scripts\python.exe" "scripts\start_app.py" %*
goto finished
:missing_python
echo Python 3.12 is required. Install Python 3.12, then double-click this file again.
pause
exit /b 1
:finished
if errorlevel 1 (
    echo.
    echo Startup stopped. Check the message above, then try again.
    pause
    exit /b 1
)
exit /b 0
