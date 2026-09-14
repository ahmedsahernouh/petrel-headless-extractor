@echo off
setlocal EnableExtensions DisableDelayedExpansion
if exist "%~dp0STANDALONE.txt" goto standalone

rem Universal, portable, read-only, no-Ocean Petrel extraction entry point.
rem Double-click to discover valid .pet/.ptd pairs beside this BAT.
rem Drag any .pet file onto this BAT, or run:
rem   run_portable_petrel_extract.bat "L:\path\Project.pet" "D:\Petrel_Extracts" inventory

set "LAUNCHER_ROOT=%~dp0"
set "TOOLKIT_ROOT=%LAUNCHER_ROOT%"
if not exist "%TOOLKIT_ROOT%scripts\invoke_portable_petrel_extract.ps1" if exist "%LAUNCHER_ROOT%..\scripts\invoke_portable_petrel_extract.ps1" set "TOOLKIT_ROOT=%LAUNCHER_ROOT%..\"
for %%T in ("%TOOLKIT_ROOT%.") do set "TOOLKIT_ROOT=%%~fT\"
set "SCRIPTS_ROOT=%TOOLKIT_ROOT%scripts"
set "OUTPUT_ROOT=%USERPROFILE%\Petrel_Extracts"
set "COMPANION_MODE=inventory"
set "PETREL_VERSION=unknown"
set "INTERACTIVE_MODE="

if /I "%~1"=="/?" goto usage
if /I "%~1"=="--help" goto usage
if "%~1"=="" goto discover_projects
set "PROJECT_FILE=%~f1"
set "PROJECT_NAME=%~n1"
if not "%~2"=="" set "OUTPUT_ROOT=%~f2"
if not "%~3"=="" set "COMPANION_MODE=%~3"
if not "%~4"=="" set "PROJECT_NAME=%~4"
if not "%~5"=="" set "PETREL_VERSION=%~5"
goto execute_project

:discover_projects
set "INTERACTIVE_MODE=1"
echo Portable Petrel read-only extractor
echo.
echo Searching for Petrel projects beside this BAT:
echo   %LAUNCHER_ROOT%
echo.
set "PROJECT_LIST=%TEMP%\petrel_project_pairs_%RANDOM%_%RANDOM%.txt"
set "PETREL_DISCOVERY_ROOT=%LAUNCHER_ROOT%"
set "PETREL_PROJECT_LIST=%PROJECT_LIST%"
powershell.exe -NoProfile -Command "$pairs = @(Get-ChildItem -LiteralPath $env:PETREL_DISCOVERY_ROOT -File -Filter '*.pet' -ErrorAction Stop | Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName ($_.BaseName + '.ptd')) -PathType Container } | Sort-Object Name); if ($pairs.Count -eq 0) { exit 4 }; $utf8 = New-Object System.Text.UTF8Encoding($false); [System.IO.File]::WriteAllLines($env:PETREL_PROJECT_LIST, [string[]]$pairs.FullName, $utf8); for ($i = 0; $i -lt $pairs.Count; $i++) { [Console]::WriteLine(('  {0}. {1}' -f ($i + 1), $pairs[$i].Name)) }"
if errorlevel 1 (
    if exist "%PROJECT_LIST%" del /q "%PROJECT_LIST%" >nul 2>&1
    echo ERROR: No complete Petrel project pair was found beside the BAT.
    echo Each project requires ProjectName.pet and ProjectName.ptd in the same folder.
    pause
    exit /b 4
)

set /a PROJECT_COUNT=0
for /f "usebackq delims=" %%P in ("%PROJECT_LIST%") do set /a PROJECT_COUNT+=1
if "%PROJECT_COUNT%"=="1" (
    for /f "usebackq delims=" %%P in ("%PROJECT_LIST%") do set "PROJECT_FILE=%%P"
    echo.
    echo One complete project was found and selected automatically.
    goto project_selected
)

:select_project
echo.
set "PROJECT_SELECTION="
set /p "PROJECT_SELECTION=Select a project number [1-%PROJECT_COUNT%]: "
if not defined PROJECT_SELECTION goto invalid_selection
for /f "delims=0123456789" %%A in ("%PROJECT_SELECTION%") do goto invalid_selection
set /a "SELECTED_NUMBER=%PROJECT_SELECTION%" >nul 2>&1
if %SELECTED_NUMBER% LSS 1 goto invalid_selection
if %SELECTED_NUMBER% GTR %PROJECT_COUNT% goto invalid_selection
set /a "PROJECT_OFFSET=SELECTED_NUMBER-1"
set "PETREL_PROJECT_OFFSET=%PROJECT_OFFSET%"
for /f "usebackq delims=" %%P in (`powershell.exe -NoProfile -Command "$items = @(Get-Content -LiteralPath $env:PETREL_PROJECT_LIST); $items[[int]$env:PETREL_PROJECT_OFFSET]"`) do set "PROJECT_FILE=%%P"
if not defined PROJECT_FILE goto invalid_selection
goto project_selected

:invalid_selection
echo ERROR: Enter a whole number from 1 to %PROJECT_COUNT%.
goto select_project

:project_selected
if exist "%PROJECT_LIST%" del /q "%PROJECT_LIST%" >nul 2>&1
for %%P in ("%PROJECT_FILE%") do set "PROJECT_NAME=%%~nP"
echo.
echo Selected project: %PROJECT_FILE%
set /p "OUTPUT_INPUT=Output root [%USERPROFILE%\Petrel_Extracts]: "
if defined OUTPUT_INPUT set "OUTPUT_ROOT=%OUTPUT_INPUT%"
set "OUTPUT_ROOT=%OUTPUT_ROOT:"=%"
for %%O in ("%OUTPUT_ROOT%") do set "OUTPUT_ROOT=%%~fO"
set /p "MODE_INPUT=Companion mode - inventory, copy, or convert [inventory]: "
if defined MODE_INPUT set "COMPANION_MODE=%MODE_INPUT%"

:execute_project
call :validate_mode
if errorlevel 1 (
    if defined INTERACTIVE_MODE pause
    exit /b 2
)
call :require_project "%PROJECT_FILE%"
if errorlevel 1 (
    if defined INTERACTIVE_MODE pause
    exit /b 3
)
call :prepare_toolkit
if errorlevel 1 (
    if defined INTERACTIVE_MODE pause
    exit /b 1
)

echo.
echo Extracting one Petrel project
echo Project: %PROJECT_FILE%
echo Output:  %OUTPUT_ROOT%
echo Mode:    %COMPANION_MODE%
echo.
call :invoke_extractor "%PROJECT_FILE%" "%PROJECT_NAME%"
if errorlevel 1 (
    if defined INTERACTIVE_MODE pause
    exit /b 1
)

echo.
echo SUCCESS: Read-only extraction completed.
echo Results root: %OUTPUT_ROOT%
if defined INTERACTIVE_MODE pause
exit /b 0

:prepare_toolkit
call :require_file "%SCRIPTS_ROOT%\initialize_portable_petrel_toolkit.ps1"
if errorlevel 1 exit /b 1
call :require_file "%SCRIPTS_ROOT%\doctor_portable_petrel_toolkit.ps1"
if errorlevel 1 exit /b 1
call :require_file "%SCRIPTS_ROOT%\invoke_portable_petrel_extract.ps1"
if errorlevel 1 exit /b 1

if not exist "%TOOLKIT_ROOT%.venv\Scripts\python.exe" (
    echo Initializing the portable toolkit for first use...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS_ROOT%\initialize_portable_petrel_toolkit.ps1"
    if errorlevel 1 (
        echo ERROR: Toolkit initialization failed.
        exit /b 1
    )
)

echo Checking the portable toolkit...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS_ROOT%\doctor_portable_petrel_toolkit.ps1"
if errorlevel 1 (
    echo ERROR: Toolkit doctor failed. No extraction was started.
    exit /b 1
)
exit /b 0

:invoke_extractor
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SCRIPTS_ROOT%\invoke_portable_petrel_extract.ps1" ^
  -ProjectFile "%~1" ^
  -OutputRoot "%OUTPUT_ROOT%" ^
  -ProjectName "%~2" ^
  -PetrelVersion "%PETREL_VERSION%" ^
  -CompanionMode "%COMPANION_MODE%"
if errorlevel 1 (
    echo ERROR: Extraction failed for %~1
    exit /b 1
)
exit /b 0

:require_project
call :require_file "%~1"
if errorlevel 1 exit /b 1
for %%P in ("%~1") do if /I not "%%~xP"==".pet" (
    echo ERROR: Project file must have a .pet extension: %~1
    exit /b 1
)
for %%P in ("%~1") do set "PROJECT_STORE=%%~dpnP.ptd"
call :require_directory "%PROJECT_STORE%"
if errorlevel 1 exit /b 1
exit /b 0

:validate_mode
if /I "%COMPANION_MODE%"=="inventory" exit /b 0
if /I "%COMPANION_MODE%"=="copy" exit /b 0
if /I "%COMPANION_MODE%"=="convert" exit /b 0
echo ERROR: Companion mode must be inventory, copy, or convert.
exit /b 1

:require_file
if exist "%~1" exit /b 0
echo ERROR: Required file not found: %~1
exit /b 1

:require_directory
pushd "%~1" >nul 2>&1
if not errorlevel 1 (
    popd
    exit /b 0
)
echo ERROR: Required directory not found: %~1
exit /b 1

:usage
echo Usage:
echo   Double-click: discover valid .pet/.ptd pairs beside this BAT and select by number.
echo   %~nx0 "PROJECT.pet" [OUTPUT_ROOT] [inventory^|copy^|convert] [PROJECT_NAME] [PETREL_VERSION]
echo.
echo You can also drag any .pet file onto this BAT.
echo The matching same-name .ptd directory must be beside the .pet file.
echo The output root must be outside the source-project directory.
exit /b 0

:standalone
if not exist "%~dp0scripts\launch_standalone_petrel.ps1" goto incomplete_standalone
if not exist "%~dp0runtime\python.exe" goto incomplete_standalone
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launch_standalone_petrel.ps1" %*
set "STANDALONE_EXIT=%errorlevel%"
if not "%STANDALONE_EXIT%"=="0" (
    echo.
    echo ERROR: The extractor could not finish. Read the error above.
    call :pause_standalone_error %*
)
exit /b %STANDALONE_EXIT%

:incomplete_standalone
echo.
echo ERROR: This standalone extraction is incomplete.
echo Required launcher scripts or the bundled Python runtime are missing.
echo Extract the whole ZIP into a short folder, for example C:\PetrelTools.
echo If Windows reports 0x80010135 - Path too long, cancel and use a shorter folder.
echo Do not skip files during extraction.
call :pause_standalone_error %*
exit /b 2

:pause_standalone_error
for %%A in (%*) do if /I "%%~A"=="-NoPause" exit /b 0
pause
exit /b 0
