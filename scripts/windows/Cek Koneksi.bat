@echo off
title Seanav - Cek Koneksi
chcp 65001 >nul
echo.
echo ===== 1. Alamat IPv4 komputer ini =====
powershell -NoProfile -Command "Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Format-Table InterfaceAlias,IPAddress -AutoSize"
echo ===== 2. Adapter yang dipakai untuk internet =====
powershell -NoProfile -Command "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } | ForEach-Object { '{0}  ->  {1}' -f $_.InterfaceAlias, $_.IPv4Address[0].IPAddress }"
echo.
echo ===== 3. Apakah server menjawab di port 8000? =====
powershell -NoProfile -Command "foreach($ip in @('127.0.0.1') + (Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }).IPAddress){ $ok = Test-NetConnection -ComputerName $ip -Port 8000 -InformationLevel Quiet -WarningAction SilentlyContinue; '{0,-16} : {1}' -f $ip, $(if($ok){'TERHUBUNG'}else{'GAGAL'}) }"
echo.
echo ===== 4. Pengaturan proxy Windows =====
powershell -NoProfile -Command "$p = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'; 'Proxy aktif : ' + [bool]$p.ProxyEnable; 'Server     : ' + $p.ProxyServer; 'Pengecualian: ' + $p.ProxyOverride; 'Skrip PAC  : ' + $p.AutoConfigURL"
echo.
echo ===== 5. Profil jaringan (Public memblokir akses dari perangkat lain) =====
powershell -NoProfile -Command "Get-NetConnectionProfile | Format-Table Name,InterfaceAlias,NetworkCategory -AutoSize"
echo ===== 6. Aturan firewall untuk Python =====
powershell -NoProfile -Command "Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue | Where-Object { $_.Program -like '*python*' } | ForEach-Object { $r = $_ | Get-NetFirewallRule; '{0} | {1} | {2} | {3}' -f $r.Action, $r.Direction, $r.Profile, $_.Program } | Select-Object -Unique"
echo.
echo Selesai. Ambil screenshot jendela ini dan kirimkan ke Claude.
pause
