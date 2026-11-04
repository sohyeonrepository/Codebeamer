@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
set "WIZARD_PY=py -3"
goto launch
:use_python
where python >nul 2>nul
if errorlevel 1 (
 echo Python 3.10 or newer with Tcl/Tk is required.
 pause
 exit /b 1
)
set "WIZARD_PY=python"
:launch
if not exist "config.local.json" (
 copy /-Y "config.json" "config.local.json" >nul
 if errorlevel 1 (
  echo Cannot create config.local.json. Existing files were not changed.
  pause
  exit /b 1
 )
)
%WIZARD_PY% import_wizard.py --config config.local.json --prompt-auth
set "WIZARD_EXIT=%ERRORLEVEL%"
if not "%WIZARD_EXIT%"=="0" pause
exit /b %WIZARD_EXIT%
