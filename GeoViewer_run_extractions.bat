@echo off
rem Copyright 2026 Ahmed Saher Nouh
rem SPDX-License-Identifier: Apache-2.0
rem Website: https://saherlabs.dev/
rem Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
setlocal EnableExtensions DisableDelayedExpansion
set "RUNNER=%~dp0GeoViewerBatch\launch_geoviewer_campaign.ps1"
if not exist "%RUNNER%" set "RUNNER=%~dp0scripts\launch_geoviewer_campaign.ps1"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%RUNNER%" -Mode run %*
set "RESULT=%ERRORLEVEL%"
echo.
echo Saved-list extraction runner exit code: %RESULT%
if not defined GEOVIEWER_BATCH_NO_PAUSE pause
exit /b %RESULT%
