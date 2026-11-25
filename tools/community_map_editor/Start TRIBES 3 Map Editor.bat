@echo off
setlocal DisableDelayedExpansion
set "entry=%~dp0Editor Files\tools\community_map_editor\start_windows.py"
if not exist "%entry%" set "entry=%~dp0start_windows.py"
if not exist "%entry%" (
    echo Extract the entire Map Tool ZIP before opening this launcher.
    pause
    exit /b 1
)
set "pythonLauncher=py.exe"
if exist "%SystemRoot%\py.exe" set "pythonLauncher=%SystemRoot%\py.exe"
if exist "%LOCALAPPDATA%\Programs\Python\Launcher\py.exe" set "pythonLauncher=%LOCALAPPDATA%\Programs\Python\Launcher\py.exe"
"%pythonLauncher%" -3.13 --version >nul 2>nul
if errorlevel 1 (
    echo Install Python 3.13 with the Windows Python launcher, then open the Map Tool again.
    if "%~1"=="" pause
    exit /b 1
)
"%pythonLauncher%" -3.13 -B "%entry%" %*
set "result=%errorlevel%"
if "%result%"=="0" exit /b 0
echo.
echo The Map Tool could not open. Check the message above and keep this window open for details.
if "%~1"=="" pause
exit /b %result%
