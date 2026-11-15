@echo off
title Seanav - Buat Akun Super Admin
chcp 65001 >nul
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] Virtual environment tidak ditemukan di webapp\.venv
  pause
  exit /b 1
)

echo.
echo  Menyiapkan tabel akun...
".venv\Scripts\python.exe" manage.py migrate --noinput >nul
if errorlevel 1 (
  echo.
  echo [!] Gagal terhubung ke database. Pastikan PostgreSQL menyala
  echo     dan password database di webapp\.env sudah benar.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" buat_admin.py
if errorlevel 1 echo [!] Akun gagal dibuat. Kirim screenshot jendela ini ke Claude.
echo.
pause
