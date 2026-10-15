@echo off
rem Copyright 2026 Ahmed Saher Nouh
rem SPDX-License-Identifier: Apache-2.0
setlocal EnableExtensions DisableDelayedExpansion
set "FV_DESKTOP_ROOT=%~dp0."
if /I "%~1"=="--smoke" goto smoke
start "Field Viewer Desktop" "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File "%~dp0desktop\FieldViewer.Desktop.ps1" -PackageRoot "%FV_DESKTOP_ROOT%"
exit /b 0

:smoke
if "%~2"=="" exit /b 2
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0desktop\FieldViewer.Desktop.ps1" -PackageRoot "%FV_DESKTOP_ROOT%" -SmokeScreenshot "%~f2"
if errorlevel 1 exit /b 1
exit /b 0
