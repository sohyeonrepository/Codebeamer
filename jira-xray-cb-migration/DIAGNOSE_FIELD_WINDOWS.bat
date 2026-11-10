@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 diagnose_field_creation.py
 goto done
:use_python
python diagnose_field_creation.py
:done
pause
