@echo off
chcp 65001 >nul
title VNC Link 1.2.0
setlocal

rem One file, no folder: everything (python, cloudflared, noVNC, TightVNC) is
rem inside dist\VNC-Link.exe. This bat is only a convenience shortcut.

set "EXE=%~dp0dist\VNC-Link.exe"
if not exist "%EXE%" (
    echo [x] VNC-Link.exe not found:
    echo     %EXE%
    echo     Build it first:  python scripts\build.py
    pause
    exit /b 1
)

start "" "%EXE%"
exit /b 0
