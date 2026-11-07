@echo off
chcp 65001 >nul
title VNC Link 1.2.0 - launcher
setlocal

rem Project-level entry point: hands over to the self-repair launcher that lives
rem inside dist\VNC-Link (the portable application folder).

set "APP=%~dp0dist\VNC-Link"
if not exist "%APP%\VNC-Link.exe" (
    echo [x] Application not found: %APP%\VNC-Link.exe
    echo     Build it first:  python scripts\build.py
    pause
    exit /b 1
)
if not exist "%APP%\bootstrap.ps1" (
    echo [x] bootstrap.ps1 missing in %APP%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%APP%\bootstrap.ps1" %*
if errorlevel 1 pause
exit /b 0
