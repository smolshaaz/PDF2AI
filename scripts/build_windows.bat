@echo off
REM Developer build: venv → pip → pytest → PyInstaller + smoke test.
REM This does NOT produce the installer. For the full release build that
REM generates release\PDF2AI-Setup.exe, run:
REM
REM   scripts\build_release_windows.bat
REM
setlocal
cd /d "%~dp0.."
if not exist .venv\Scripts\python.exe (
  py -3.13 -m venv .venv
  if errorlevel 1 exit /b 1
)
.venv\Scripts\python.exe -m pip install -c requirements-runtime.txt -e ".[dev,build]"
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 exit /b 1
.venv\Scripts\python.exe scripts\build.py
if errorlevel 1 exit /b 1
echo Build and packaged smoke test completed. See dist.
