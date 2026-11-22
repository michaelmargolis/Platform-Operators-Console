rem run_harness.bat ver 1
@echo off
cd /d "%~dp0"
py -3 harness.py
if errorlevel 1 pause
