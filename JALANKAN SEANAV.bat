@echo off
title Seanav Operations (biarkan jendela ini terbuka)
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [!] Aplikasi belum disiapkan di komputer ini.
  echo     Jalankan "scripts\windows\1. Setup (sekali saja).bat" terlebih dahulu.
  pause
  exit /b 1
)
set "PY=.venv\Scripts\python.exe"

rem --- Pasang paket hanya bila requirements.txt berubah (agar cepat) ---
fc /b requirements.txt ".venv\requirements.terpasang" >nul 2>&1
if errorlevel 1 (
  echo  Memasang / memperbarui paket Python...
  "%PY%" -m pip install -q -r requirements.txt
  if errorlevel 1 (
    echo [!] Gagal memasang paket. Pastikan laptop terhubung internet lalu coba lagi.
    pause
    exit /b 1
  )
  copy /y requirements.txt ".venv\requirements.terpasang" >nul
)
echo  Menyiapkan database...
"%PY%" manage.py migrate --noinput >nul
if errorlevel 1 (
  echo [!] Database belum siap. Pastikan PostgreSQL berjalan, lalu coba lagi.
  pause
  exit /b 1
)

rem --- Buat shortcut di Desktop (sekali saja) ---
powershell -NoProfile -Command "$d=[Environment]::GetFolderPath('Desktop'); $l=Join-Path $d 'Seanav Operations.lnk'; if(-not(Test-Path $l)){ $s=(New-Object -ComObject WScript.Shell).CreateShortcut($l); $s.TargetPath='%~f0'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%SystemRoot%\System32\shell32.dll,13'; $s.Save() }" >nul 2>&1

cls
set "BUKA=--open"

:ulang
"%PY%" manage.py runserver_https %BUKA%
if errorlevel 3 (
  timeout /t 3 >nul
  exit /b 0
)
if errorlevel 2 (
  pause
  exit /b 2
)
set "BUKA="
echo.
echo  Server berhenti. Menyalakan ulang dalam 3 detik... (tutup jendela ini untuk berhenti)
timeout /t 3 /nobreak >nul
goto ulang
