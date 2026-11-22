@echo off
rem run_canonical_ride_capture.bat ver 1
setlocal
cd /d "%~dp0.."

echo MDX canonical NoLimits ride capture
echo -----------------------------------
py tools\create_canonical_rides.py
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Canonical capture finished with exit code %RC%.
pause
exit /b %RC%
