@echo off
title Seanav Operations - Server HTTPS
chcp 65001 >nul
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] Aplikasi belum disiapkan. Jalankan "1. Setup (sekali saja).bat" dulu.
  pause
  exit /b 1
)

echo  Memeriksa paket Python...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" manage.py migrate --noinput >nul

cls
echo.
echo  ==============================================================
echo    SEANAV OPERATIONS  -  mode HTTPS (kamera dan GPS aktif)
echo  ==============================================================
echo    Gunakan alamat https:// di bawah dari HP atau laptop lain.
echo    Pertama kali dibuka akan muncul peringatan keamanan: pilih
echo    "Advanced / Lanjutan" lalu "Proceed / Lanjutkan ke situs".
echo    Bila Windows menanyakan izin firewall, pilih "Allow access".
echo  ==============================================================

start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 5; Start-Process 'https://localhost:8443/'"
".venv\Scripts\python.exe" manage.py runserver_https --port 8443
pause
