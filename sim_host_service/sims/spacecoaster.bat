rem spacecoaster.bat ver 2
@echo off
echo Space Coaster
echo Restarting Oculus OVRService before SpaceCoaster launch...
powershell.exe -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; Restart-Service -Name 'OVRService' -Force; $svc=Get-Service -Name 'OVRService'; $svc.WaitForStatus('Running',[TimeSpan]::FromSeconds(20)); if ($svc.Status -ne 'Running') { throw 'OVRService did not reach Running state' }"
if errorlevel 1 (
  echo ERROR: OVRService restart failed. Sim Host Service must run elevated.
  exit /b 1
)
timeout /t 2 /nobreak >nul
start "" "C:\Spacecoaster\CoasterMSU.exe"
