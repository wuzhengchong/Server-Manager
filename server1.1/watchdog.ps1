# Aiwo (LoveVodyanitsa) server watchdog
# Restart policy: prefer the elevated scheduled task "AiwoServer"; fall back to direct start.
$serverPath = 'd:\server\server1.1\server.py'
$pythonw = 'C:\Users\wzc\AppData\Local\Programs\Python\Python313\pythonw.exe'

while ($true) {
    $p = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*server1.1*server.py*' }
    if (-not $p) {
        $hasTask = schtasks /query /tn AiwoServer 2>$null
        if ($LASTEXITCODE -eq 0) {
            schtasks /run /tn AiwoServer | Out-Null
        }
        Start-Sleep -Seconds 10
        $p = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe'" |
            Where-Object { $_.CommandLine -like '*server1.1*server.py*' }
        if (-not $p) {
            # task missing/denied -> direct fallback start
            Start-Process $pythonw -ArgumentList $serverPath
        }
    }
    Start-Sleep -Seconds 60
}
