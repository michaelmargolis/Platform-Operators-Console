rem start_shs.cmd ver 3
@echo off
setlocal

set "SHS_ROOT=%~dp0..\.."
set "SHS_CONFIG=%SHS_ROOT%\sim_host_service\sim_host_service.ini"
set "SHS_LOG_DIR=%LOCALAPPDATA%\POC\logs"
set "SHS_LOG=%SHS_LOG_DIR%\shs.log"

if not exist "%SHS_LOG_DIR%" mkdir "%SHS_LOG_DIR%"
if exist "%SHS_LOG%" for %%A in ("%SHS_LOG%") do if %%~zA GTR 10485760 move /Y "%SHS_LOG%" "%SHS_LOG%.1" >nul
pushd "%SHS_ROOT%" || exit /b 1

echo.>>"%SHS_LOG%"
echo [%date% %time%] Starting POC Sim Host Service>>"%SHS_LOG%"
py -3 -u -m sim_host_service.server --config "%SHS_CONFIG%" >>"%SHS_LOG%" 2>&1
set "SHS_EXIT=%ERRORLEVEL%"
echo [%date% %time%] SHS stopped with exit code %SHS_EXIT%>>"%SHS_LOG%"

popd
exit /b %SHS_EXIT%
