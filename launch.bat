@echo off
rem Windows: double-click to start the dashboard.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment ...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 (
    echo Python 3.11 or newer is required: https://www.python.org/downloads/
    pause
    exit /b 1
  )
)

fc /b requirements.txt ".venv\requirements.installed" >nul 2>&1
if errorlevel 1 (
  echo Installing requirements, the first time takes a few minutes ...
  ".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
  ".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
  if errorlevel 1 ( pause & exit /b 1 )
  copy /y requirements.txt ".venv\requirements.installed" >nul
)

start "" cmd /c "timeout /t 4 >nul & start http://localhost:8501"
echo Starting dashboard at http://localhost:8501 (close this window to stop it) ...
".venv\Scripts\python.exe" -m streamlit run dashboard\app.py --server.headless true --server.port 8501
pause
