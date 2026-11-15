@echo off
title Seanav Operations - Server
chcp 65001 >nul
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] Aplikasi belum disiapkan di komputer ini.
  echo     Jalankan "1. Setup (sekali saja).bat" terlebih dahulu.
  pause
  exit /b 1
)

echo  Memeriksa paket Python...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" manage.py migrate --noinput >nul

set "PORT=8000"
set "IP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "$c = Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } | Select-Object -First 1; if ($c) { $c.IPv4Address[0].IPAddress }"`) do set "IP=%%i"
if "%IP%"=="" set "IP=127.0.0.1"

cls
echo.
echo  ==============================================================
echo    SEANAV OPERATIONS  -  Oseana ^& Seanav
echo  ==============================================================
echo.
echo    Buka dari komputer ini atau komputer lain di jaringan yang sama:
echo.
echo    Halaman utama  : http://%IP%:%PORT%/
echo    Login staf     : http://%IP%:%PORT%/login/
echo    Dashboard      : http://%IP%:%PORT%/dashboard/
echo    Inventaris     : http://%IP%:%PORT%/inventaris/
echo    Quotation      : http://%IP%:%PORT%/quotation/
echo    Invoice        : http://%IP%:%PORT%/invoice/
echo    Rental         : http://%IP%:%PORT%/rental/
echo    Proyek         : http://%IP%:%PORT%/proyek/
echo    Admin Django   : http://%IP%:%PORT%/admin/
echo.
echo    Biarkan jendela ini terbuka selama aplikasi dipakai.
echo    Tekan Ctrl+C untuk menghentikan server.
echo  ==============================================================
echo.

start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 4; Start-Process 'http://%IP%:%PORT%/'"
".venv\Scripts\python.exe" manage.py runserver 0.0.0.0:%PORT%
pause
