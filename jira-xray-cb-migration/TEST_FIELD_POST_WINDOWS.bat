@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto use_python
py -3 test_field_post_once.py
goto done
:use_python
python test_field_post_once.py
:done
pause
