@echo off
title Seanav Operations - Server HTTPS (JANGAN DITUTUP)
chcp 65001 >nul
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] Aplikasi belum disiapkan. Jalankan "1. Setup (sekali saja).bat" dulu.
  pause
  exit /b 1
)

echo  Memeriksa paket Python...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (
  echo [!] Gagal memasang paket. Pastikan laptop terhubung internet lalu coba lagi.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" manage.py migrate --noinput >nul

cls
echo.
echo  ==============================================================
echo    SEANAV OPERATIONS  -  mode HTTPS (kamera dan GPS aktif)
echo  ==============================================================
echo    BIARKAN JENDELA INI TERBUKA selama web dipakai.
echo    Jangan klik di dalam jendela ini (bisa membuat server terhenti).
echo.
echo    Pertama kali dibuka akan muncul peringatan keamanan: pilih
echo    "Advanced / Lanjutan" lalu "Continue / Lanjutkan ke situs".
echo    Bila Windows menanyakan izin firewall, pilih "Allow access".
echo  ==============================================================

start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 6; Start-Process 'https://localhost:8443/'"

:ulang
".venv\Scripts\python.exe" manage.py runserver_https --port 8443
if errorlevel 2 (
  pause
  exit /b 2
)
echo.
echo  Server berhenti. Menyalakan ulang dalam 3 detik... (tutup jendela untuk berhenti)
timeout /t 3 /nobreak >nul
goto ulang
