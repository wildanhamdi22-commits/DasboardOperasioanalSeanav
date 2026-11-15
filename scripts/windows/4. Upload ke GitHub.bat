@echo off
title Seanav - Upload ke GitHub
chcp 65001 >nul
cd /d "%~dp0..\.."
set "REPO=https://github.com/wildanhamdi22-commits/DasboardOperasioanalSeanav.git"

where git >nul 2>nul
if errorlevel 1 (
  echo [!] Git belum terpasang. Pasang dulu:  winget install --id Git.Git -e
  echo     atau unduh dari https://git-scm.com/download/win , lalu jalankan file ini lagi.
  pause
  exit /b 1
)

if not exist ".git" git init -b main >nul

for /f "delims=" %%n in ('git config user.name') do set "GN=%%n"
if "%GN%"=="" (
  set /p GN=Nama untuk riwayat Git:
  call git config user.name "%%GN%%"
)
for /f "delims=" %%e in ('git config user.email') do set "GE=%%e"
if "%GE%"=="" (
  set /p GE=Email akun GitHub:
  call git config user.email "%%GE%%"
)

git remote remove origin >nul 2>nul
git remote add origin %REPO%

echo.
echo  File yang akan di-upload (.env, media, dan cadangan TIDAK ikut):
git add -A
git status --short
echo.
git commit -m "Seanav Operations: dashboard baru, semua modul, absensi & izin, siap hosting" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01MqaHFxpcQ2oKgmXrpNT1bQ" >nul 2>nul

git fetch origin >nul 2>nul
git rev-parse --verify origin/main >nul 2>nul
if not errorlevel 1 (
  echo  Menggabungkan dengan isi repo yang sudah ada di GitHub...
  git pull origin main --allow-unrelated-histories --no-edit -X ours
)

echo  Mengunggah... (jendela login GitHub bisa muncul - masuk dengan akun Anda)
git push -u origin main
if errorlevel 1 (
  echo.
  echo [!] Upload gagal. Pastikan Anda login dengan akun yang punya akses ke repo tersebut.
) else (
  echo.
  echo  BERHASIL. Buka https://github.com/wildanhamdi22-commits/DasboardOperasioanalSeanav
)
pause
