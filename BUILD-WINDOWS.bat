@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title InvoSift Windows Builder

echo ========================================
echo          InvoSift Windows Builder
echo ========================================
echo.

where node >nul 2>nul
if errorlevel 1 goto no_node
where npm >nul 2>nul
if errorlevel 1 goto no_npm

if not exist "build\icon.ico" goto no_icon

echo Checking Python 3.12 x64...
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys,struct; assert sys.version_info[:2]==(3,12) and struct.calcsize('P')==8" >nul 2>nul
if errorlevel 1 goto wrong_python

echo.
echo Installing Electron project dependencies...
call npm install --include=dev --ignore-scripts=false
if errorlevel 1 goto failed

echo.
echo Enabling and verifying the Electron runtime...
call npm install-scripts approve electron >nul 2>nul
call npm install-scripts approve electron-winstaller >nul 2>nul
call npm rebuild electron
if errorlevel 1 goto electron_failed
call npx electron --version
if errorlevel 1 goto electron_failed

echo.
echo Building the Python backend and Windows applications (x64)...
echo The first backend build downloads OCR and Python dependencies and may take time.
call npm run dist
if errorlevel 1 goto failed

if not exist "release" goto no_release
dir /b "release\*Setup*.exe" >nul 2>nul
if errorlevel 1 goto no_installer
dir /b "release\*Portable*.exe" >nul 2>nul
if errorlevel 1 goto no_portable

echo.
echo Build complete. Opening the release folder:
echo - InvoSift NSIS installer (.exe)
echo - InvoSift portable executable (.exe)
explorer "%~dp0release"
pause
exit /b 0

:no_node
echo ERROR: Node.js is not installed or is not on PATH.
echo Install Node.js LTS, then reopen this script.
goto failed
:no_npm
echo ERROR: npm was not found. Reinstall Node.js with npm enabled.
goto failed
:no_python
echo ERROR: Python 3.12 x64 was not found on PATH.
echo Install Python 3.12 x64 and enable Add Python to PATH.
goto failed
:wrong_python
echo ERROR: The first Python on PATH must be 64-bit Python 3.12.
echo Run "py -3.12 -c "import sys; print(sys.version)"" to inspect your Python installation.
goto failed
:electron_failed
echo ERROR: Electron's install script did not install its Windows runtime.
echo Try: npm install-scripts approve electron
echo Then: npm rebuild electron
goto failed
:no_icon
echo ERROR: build\icon.ico is missing. Restore the InvoSift Windows icon asset before building.
goto failed
:no_release
echo ERROR: Build exited successfully but the release folder was not created.
goto failed
:no_installer
echo ERROR: The NSIS installer was not found in release.
goto failed
:no_portable
echo ERROR: The portable EXE was not found in release.
goto failed
:failed
echo.
echo BUILD FAILED. No successful build is being reported.
echo Read the first error above. Existing release files have not been declared current.
pause
exit /b 1
