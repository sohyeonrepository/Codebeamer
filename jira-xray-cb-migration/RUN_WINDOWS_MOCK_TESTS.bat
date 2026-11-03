@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m unittest discover -s tests -v
) else (
  python -m unittest discover -s tests -v
)
set "TEST_EXIT=%ERRORLEVEL%"
echo Exit code: %TEST_EXIT%
pause
exit /b %TEST_EXIT%
