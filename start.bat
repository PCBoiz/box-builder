@echo off
rem Box builder launcher for Windows. Double-click, then open http://localhost:8080
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul

where docker >nul 2>nul
if %errorlevel%==0 (
  echo Starting with Docker...
  docker compose up -d --build
  echo.
  echo Open http://localhost:8080   (stop with: docker compose down)
  pause
  exit /b
)

where python >nul 2>nul
if not %errorlevel%==0 (
  echo Neither Docker nor Python found. Install Docker Desktop or Python 3.12 and run this again.
  pause
  exit /b 1
)

if not exist .venv python -m venv .venv
call .venv\Scripts\activate.bat
pip install -q -r requirements.txt
set DB_PATH=data\boxes.db
set CATALOG_PATH=data\catalog.csv
echo Open http://localhost:8080   (Ctrl+C to stop)
uvicorn app.main:app --host 127.0.0.1 --port 8080
