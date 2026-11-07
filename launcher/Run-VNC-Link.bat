@echo off
chcp 65001 >nul
title VNC Link 1.2.0 - launcher
setlocal

rem One-click entry point. It lives next to bootstrap.ps1 inside the app folder,
rem so the whole folder can be copied to any Windows 10/11 machine and run.

set "HERE=%~dp0"
if not exist "%HERE%bootstrap.ps1" (
    echo [x] bootstrap.ps1 not found next to this file.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%bootstrap.ps1" %*
if errorlevel 1 pause
exit /b 0
