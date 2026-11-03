@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 windows_live_test.py
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python was not found. Install Python 3.12 from python.org with Tcl/Tk.
    pause
    exit /b 1
  )
  python windows_live_test.py
)
set "TEST_EXIT=%ERRORLEVEL%"
echo Exit code: %TEST_EXIT%
pause
exit /b %TEST_EXIT%
