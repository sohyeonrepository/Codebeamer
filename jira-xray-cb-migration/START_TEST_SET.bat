@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
if not exist "logs" mkdir "logs"
set "LOG=%CD%\logs\last_test_set_run.log"
echo ==============================================
echo    TEST SET - SIMPLE ONE ISSUE TEST
echo ==============================================
echo.
echo This starts with PREVIEW ONLY. No POST will be sent.
echo Results and errors are saved to:
echo   logs\last_test_set_run.log
echo.
where python >nul 2>nul
if errorlevel 1 (
 echo [ERROR] Python is not on PATH.
 echo [ERROR] Python not found. > "%LOG%"
 goto FINISH
)
if not exist "config.local.json" (
 echo [ERROR] config.local.json is missing in this folder.
 echo [ERROR] Missing config.local.json > "%LOG%"
 goto FINISH
)
set "PROJECT=STGVSTAR"
echo Jira project key (press Enter for STGVSTAR):
set /p "PROJECT_INPUT=> "
if defined PROJECT_INPUT set "PROJECT=%PROJECT_INPUT%"
set "TRACKER="
echo.
echo Codebeamer TEST SET tracker numeric ID:
set /p "TRACKER=> "
if not defined TRACKER (
 echo [ERROR] Tracker ID is required.
 echo [ERROR] Tracker ID missing > "%LOG%"
 goto FINISH
)
echo.
echo Running read-only preview of ONE Test Set...
python -u batch_case_set_import.py --kind set --jira-project "%PROJECT%" --tracker-id "%TRACKER%" --limit 1 --config config.local.json --prompt-auth > "%LOG%" 2>&1
set "RESULT=%ERRORLEVEL%"
echo.
type "%LOG%"
echo.
if not "%RESULT%"=="0" (
 echo [FAILED] Preview did not complete. NO POST requested.
 echo Share logs\last_test_set_run.log without credentials or private issue contents.
 goto FINISH
)
echo [OK] Read-only preview completed. NO ITEM WAS CREATED.
echo.
echo Real POST is disabled in this starter to avoid accidental partial imports.
echo Continue with a single Item test only after reviewing field coverage.
:FINISH
echo.
echo Window will stay open so you can read any error.
pause
endlocal
