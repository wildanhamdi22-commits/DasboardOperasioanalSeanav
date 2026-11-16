# Hentikan server Seanav yang masih berjalan (versi lama / jendela tersembunyi) beserta jendela cmd-nya.
$procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" |
  Where-Object { $_.CommandLine -match 'manage\.py' -and $_.CommandLine -match 'runserver' }
$count = 0
foreach ($p in $procs) {
  $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
  Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  $count++
  if ($parent -and $parent.Name -eq 'cmd.exe' -and $parent.ProcessId -ne $PID) {
    Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue
  }
}
if ($count -gt 0) {
  Write-Host "  Server lama dihentikan ($count proses)."
  Start-Sleep -Seconds 2
}
