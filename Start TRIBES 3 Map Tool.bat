@echo off
setlocal DisableDelayedExpansion
title TRIBES 3 Map Tool
set "launcher=%~dp0tools\community_map_editor\Start TRIBES 3 Map Editor.bat"
if not exist "%launcher%" (
    echo Extract the complete TRIBES 3 Map Tool ZIP before opening this launcher.
    if "%~1"=="" pause
    exit /b 1
)
call "%launcher%" %*
exit /b %errorlevel%
