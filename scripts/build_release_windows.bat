@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0.."
echo.
echo =====================================================================
echo  PDF2AI — Full Release Build
echo  Produces: release\PDF2AI-Setup.exe
echo =====================================================================
echo.

REM ── 1. Verify 64-bit Python ─────────────────────────────────────────────
echo [1/7] Verifying 64-bit Python...
set "PYCMD=py -3"
py -3 --version >nul 2>&1
if errorlevel 1 (
    set "PYCMD=python"
)
%PYCMD% -c "import struct, sys; bits=struct.calcsize('P')*8; print(f'Python {sys.version.split()[0]} ({bits}-bit)'); sys.exit(0 if bits==64 else 1)"
if errorlevel 1 (
    echo.
    echo ERROR: A 64-bit Python 3.x interpreter is required to build PDF2AI.
    echo        The currently selected Python is 32-bit, which will produce a
    echo        32-bit application that cannot load 64-bit native libraries.
    echo.
    echo        Install 64-bit Python 3.13 from https://python.org and retry.
    echo.
    exit /b 1
)

REM ── 2. Create / reuse virtual environment ───────────────────────────────
echo.
echo [2/7] Setting up virtual environment...
if not exist .venv\Scripts\python.exe (
    %PYCMD% -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        exit /b 1
    )
)
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
if errorlevel 1 exit /b 1

REM ── 3. Install pinned dependencies ──────────────────────────────────────
echo.
echo [3/7] Installing pinned dependencies...
.venv\Scripts\python.exe -m pip install --quiet -e ".[dev,build]"
if errorlevel 1 (
    echo ERROR: pip install failed. Check your internet connection and pyproject.toml.
    exit /b 1
)

REM ── 4. Run test suite ───────────────────────────────────────────────────
echo.
echo [4/7] Running test suite...
.venv\Scripts\python.exe -m pytest -q
if errorlevel 1 (
    echo ERROR: One or more tests failed. Fix all failures before building a release.
    exit /b 1
)

REM ── 5. PyInstaller build + packaged smoke test ───────────────────────────
echo.
echo [5/7] Building PyInstaller application + packaged smoke test...
.venv\Scripts\python.exe scripts\build.py
if errorlevel 1 (
    echo ERROR: PyInstaller build or packaged self-test failed.
    echo        Check .build\packaged-smoke\FAIL.txt for details.
    exit /b 1
)
if not exist dist\PDF2AI\PDF2AI.exe (
    echo ERROR: dist\PDF2AI\PDF2AI.exe was not produced. Do not distribute.
    exit /b 1
)

REM ── 6. Locate Inno Setup compiler ───────────────────────────────────────
echo.
echo [6/7] Locating Inno Setup compiler (ISCC.exe)...
set "ISCC="
for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles(x86)%\Inno Setup 5\ISCC.exe"
    "%ProgramFiles%\Inno Setup 5\ISCC.exe"
) do (
    if exist %%P (
        set "ISCC=%%~P"
        goto :found_iscc
    )
)

:iscc_not_found
echo.
echo ERROR: Inno Setup compiler (ISCC.exe) was not found.
echo.
echo        Inno Setup is required on the BUILD MACHINE only.
echo        End users do NOT need it.
echo.
echo        To install Inno Setup:
echo          1. Open https://jrsoftware.org/isdl.php in your browser.
echo          2. Download "Inno Setup 6.x.x (issetup.exe)" — stable release.
echo          3. Run issetup.exe and accept defaults.
echo          4. Re-run this script.
echo.
exit /b 1

:found_iscc
echo        Found: %ISCC%

REM ── 7. Build installer ──────────────────────────────────────────────────
echo.
echo [7/7] Compiling Inno Setup installer...
if not exist release mkdir release
"%ISCC%" /Q installer\PDF2AI.iss
if errorlevel 1 (
    echo ERROR: Inno Setup compilation failed. Check the .iss file for errors.
    exit /b 1
)
if not exist release\PDF2AI-Setup.exe (
    echo ERROR: release\PDF2AI-Setup.exe was not produced. Check Inno Setup output.
    exit /b 1
)

echo.
echo =====================================================================
echo  BUILD COMPLETE
echo.
echo  Release artifact:
echo    %CD%\release\PDF2AI-Setup.exe
echo.
echo  Distribute ONLY this file. The installer packages everything the
echo  end user needs. No Python installation is required on the target PC.
echo.
echo  Before broad distribution:
echo    - Test on a CLEAN 64-bit Windows PC where Python is NOT installed.
echo    - Follow the "Release checklist" in README.md.
echo    - Optional: sign release\PDF2AI-Setup.exe with a code-signing cert.
echo =====================================================================
echo.
endlocal
