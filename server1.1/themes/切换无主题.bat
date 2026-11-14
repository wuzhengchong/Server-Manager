@echo off
rem Switch homepage to PLAIN theme ("My Server", no branding, no background)
copy /Y "d:\server\server1.1\themes\index_plain.html" "d:\server\server1.1\rooooot\index.html" >nul
echo Plain theme applied. Refresh browser to see it.
pause
