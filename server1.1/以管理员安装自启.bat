@echo off
rem Install elevated autostart task (approve the UAC prompt)
rem After this, the server runs with admin rights so remote input works in admin/UAC apps.
powershell -NoProfile -Command "Start-Process powershell -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','d:\server\server1.1\install_task.ps1' -Verb RunAs"
echo Request sent. Approve the UAC prompt to install.
