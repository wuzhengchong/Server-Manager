# Register elevated autostart task for Aiwo server (run as admin)
$action = New-ScheduledTaskAction -Execute 'C:\Users\wzc\AppData\Local\Programs\Python\Python313\pythonw.exe' -Argument 'd:\server\server1.1\server.py'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User 'wzc'
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName 'AiwoServer' -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -Force

# Stop the non-elevated instance if any, then start the elevated one
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*server1.1*server.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Start-Sleep -Seconds 1
Start-ScheduledTask -TaskName 'AiwoServer'
Start-Sleep -Seconds 3
$p = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | Where-Object { $_.CommandLine -like '*server1.1*server.py*' }
if ($p) { Write-Host 'OK: elevated server is running (PID' $p.ProcessId ')' } else { Write-Host 'WARN: task registered but server not detected yet' }
Pause
