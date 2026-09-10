@echo off
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\pythonw.exe (
  echo Create .venv and install the project first. See README.md.
  pause
  exit /b 1
)
start "PDF2AI" ".venv\Scripts\pythonw.exe" -m pdf2ai
