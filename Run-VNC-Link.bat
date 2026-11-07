@echo off
rem ---------------------------------------------------------------
rem  VNC Link 1.2.0 - portable launcher
rem  The application itself lives in dist\VNC-Link\VNC-Link.exe
rem  Copy the whole dist\VNC-Link folder if you move it elsewhere.
rem ---------------------------------------------------------------
set "APP=%~dp0dist\VNC-Link\VNC-Link.exe"
if not exist "%APP%" (
    echo [x] VNC-Link.exe not found:
    echo     %APP%
    echo     Build it first:  python scripts\build.py
    pause
    exit /b 1
)
start "" "%APP%"
exit /b 0
