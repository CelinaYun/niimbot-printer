@echo off
rem NIIMBOT Print Assistant launcher
rem Finds a real Python (the Microsoft Store stub is rejected), creates a
rem virtualenv on first run, installs deps when missing, then starts the GUI.
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "VENV=%~dp0.venv"
set "PY=%VENV%\Scripts\python.exe"

if exist "%PY%" goto checkdeps

echo [1/3] Creating virtual environment...
call :findbase
if not defined BASE (
  echo.
  echo ============================================================
  echo   Python 3.10 or newer was NOT found on this computer.
  echo ============================================================
  echo.
  echo   To install Python:
  echo     1. Go to  https://www.python.org/downloads/
  echo     2. Download Python 3.10 or newer
  echo     3. Run the installer
  echo        ** TICK the box "Add python.exe to PATH" **
  echo     4. Run this launcher again
  echo.
  echo   Note: the "python.exe" that comes from the Microsoft
  echo   Store is only a placeholder and cannot be used.
  echo   See the readme file in this folder for details.
  echo.
  pause
  exit /b 1
)
echo       Using: %BASE%
%BASE% -m venv "%VENV%"
if not exist "%PY%" (
  echo Failed to create the virtual environment.
  pause
  exit /b 1
)

:checkdeps
echo [2/3] Checking dependencies...
"%PY%" -c "import bleak, serial, PIL, qrcode, barcode, tkinter" >nul 2>nul
if not errorlevel 1 goto run
echo       Installing, this happens only once...
"%PY%" -m pip install --disable-pip-version-check -q -r "%~dp0requirements.txt"
if errorlevel 1 (
  echo.
  echo Dependency install failed. Check your network connection and retry.
  pause
  exit /b 1
)

:run
echo [3/3] Starting NIIMBOT Print Assistant...
"%PY%" "%~dp0main.py"
if errorlevel 1 (
  echo.
  echo The program exited with an error. See the message above.
  pause
)
endlocal
exit /b 0

rem ----------------------------------------------------------------
rem :findbase  - set BASE to a working Python 3.10+ interpreter
rem The Microsoft Store placeholder exits with code 9009 and is rejected.
rem ----------------------------------------------------------------
:findbase
set "BASE="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "BASE=py -3"
)
if defined BASE goto :eof

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys" >nul 2>nul
  if not errorlevel 1 set "BASE=python"
)
if defined BASE goto :eof

where python3 >nul 2>nul
if not errorlevel 1 (
  python3 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "BASE=python3"
)
if defined BASE goto :eof

for %%P in (
  "%LocalAppData%\Programs\Python\Python313\python.exe"
  "%LocalAppData%\Programs\Python\Python312\python.exe"
  "%LocalAppData%\Programs\Python\Python311\python.exe"
  "%LocalAppData%\Programs\Python\Python310\python.exe"
  "%LocalAppData%\Programs\Python\Python39\python.exe"
  "C:\Python313\python.exe"
  "C:\Python312\python.exe"
  "C:\Python311\python.exe"
  "C:\Python310\python.exe"
  "C:\Program Files\Python313\python.exe"
  "C:\Program Files\Python312\python.exe"
  "C:\Program Files\Python311\python.exe"
  "C:\Program Files\Python310\python.exe"
  "C:\Program Files (x86)\Python311\python.exe"
  "C:\Program Files (x86)\Python310\python.exe"
) do (
  if not defined BASE (
    if exist %%P (
      %%P -c "import sys" >nul 2>nul
      if not errorlevel 1 set "BASE=%%~P"
    )
  )
)
goto :eof
