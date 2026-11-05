rem run_tests.bat ver 1
@echo off
cd /d "%~dp0"
py -3 -m unittest discover -s tests -v
pause
