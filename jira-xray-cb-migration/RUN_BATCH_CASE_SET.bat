@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo [1] Test Case preview
echo [2] Test Set preview
echo [3] Test Case batch import (real POST)
echo [4] Test Set batch import (real POST)
set /p MODE=Choose 1-4:
if "%MODE%"=="1" (set "KIND=case" & set "EXEC=0")
if "%MODE%"=="2" (set "KIND=set" & set "EXEC=0")
if "%MODE%"=="3" (set "KIND=case" & set "EXEC=1")
if "%MODE%"=="4" (set "KIND=set" & set "EXEC=1")
if not defined KIND (
 echo Invalid selection
 goto FINISH
)
set /p PROJECT=Jira project key:
set /p TRACKER=Existing CB tracker numeric ID:
set /p LIMIT=Maximum issues ^(0=all; first run recommended 1^):
if "%LIMIT%"=="" set LIMIT=1
set "EXTRA="
set /p CHOICE=Include Choice/Option mappings? ^(Y/N; default N^):
if /I "%CHOICE%"=="Y" set "EXTRA=%EXTRA% --include-options"
set /p USERS=Include User/Assignee mappings? ^(Y/N; default N^):
if /I "%USERS%"=="Y" set "EXTRA=%EXTRA% --include-users"
set /p REL=Capture Xray relations for phase 2? ^(Y/N; default N^):
if /I "%REL%"=="Y" set "EXTRA=%EXTRA% --capture-relations"
set /p CHILDREN=Capture comments and attachment metadata for later import? ^(Y/N; default N^):
if /I "%CHILDREN%"=="Y" set "EXTRA=%EXTRA% --capture-children"
if "%EXEC%"=="1" (
 echo WARNING: This sends real POST requests to Codebeamer.
 echo IMPORTANT: Choice/User only when selected; comments, attachments, relation writes and history are NOT included.
 echo This is a PARTIAL Item write. Children and Test Set membership are separate scripts.
 set /p PARTIAL=Type PARTIAL to accept incomplete migration:
 if /I not "!PARTIAL!"=="PARTIAL" goto FINISH
 set /p CONFIRM=Retype Jira project key to confirm:
 python batch_case_set_import.py --kind %KIND% --jira-project "%PROJECT%" --tracker-id %TRACKER% --limit %LIMIT% --config config.local.json --prompt-auth %EXTRA% --acknowledge-partial --execute --confirm-project "!CONFIRM!"
) else (
 python batch_case_set_import.py --kind %KIND% --jira-project "%PROJECT%" --tracker-id %TRACKER% --limit %LIMIT% --config config.local.json --prompt-auth %EXTRA%
)
echo.
echo Check output and .migration\test-case-set-batch journal before retrying.
:FINISH
echo.
echo [INFO] The window will stay open for errors.
pause
endlocal
