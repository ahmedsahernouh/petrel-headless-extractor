@echo off
rem Copyright 2026 Ahmed Saher Nouh
rem SPDX-License-Identifier: Apache-2.0
rem Website: https://saherlabs.dev/
rem Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
setlocal EnableExtensions DisableDelayedExpansion
echo Website: https://saherlabs.dev/
echo Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
echo.
if not exist "%~dp0scripts\launch_zgy_conversion.ps1" goto incomplete
if not exist "%~dp0STANDALONE.txt" goto incomplete
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_zgy_conversion.ps1" %*
set "CONVERSION_EXIT=%errorlevel%"
if not "%CONVERSION_EXIT%"=="0" (
    echo Conversion did not complete. Read the error above.
    for %%A in (%*) do if /I "%%~A"=="-NoPause" exit /b %CONVERSION_EXIT%
    pause
)
exit /b %CONVERSION_EXIT%
:incomplete
echo ERROR: Extract the complete standalone ZIP; the BAT alone cannot run.
for %%A in (%*) do if /I "%%~A"=="-NoPause" exit /b 2
pause
exit /b 2
