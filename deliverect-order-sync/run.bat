@echo off
REM Deliverect Order Sync — Windows launcher
REM Usage: run.bat [command] [options]
REM Examples:
REM   run.bat login
REM   run.bat calibrate
REM   run.bat run
REM   run.bat export
REM   run.bat import-file path\to\export.csv
REM   run.bat status
REM   run.bat reauthenticate

setlocal

if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: Virtual environment not found.
    echo Run: python -m venv .venv
    echo Then: .venv\Scripts\activate
    echo Then: pip install -e ".[dev]"
    exit /b 1
)

call .venv\Scripts\activate.bat
python -m deliverect_sync %*
