@echo off
title Seanav - Hapus Data Uji Coba
chcp 65001 >nul
cd /d "%~dp0..\.."

if not exist ".venv\Scripts\python.exe" (
  echo [!] Virtual environment tidak ditemukan di webapp\.venv
  pause
  exit /b 1
)

echo.
echo  ============================================================
echo   HAPUS DATA UJI COBA - aplikasi siap diisi dari awal
echo  ============================================================
echo   Dihapus  : quotation, invoice, rental, serah terima, pesanan,
echo              QC, proyek survey, laporan harian, arsip dokumen,
echo              riwayat absensi dan izin/cuti (beserta fotonya)
echo   Tetap ada: akun login, role, data perusahaan, kategori alat
echo   Backup   : otomatis disalin ke folder webapp\backup_reset_...
echo  ============================================================
echo.
echo  Tutup dulu jendela "Jalankan Seanav" bila sedang terbuka.
echo.
".venv\Scripts\python.exe" manage.py migrate --noinput >nul

set ALAT=
set KLIEN=
choice /C YN /M "Hapus juga DAFTAR ALAT RENTAL dan KATALOG PRODUK"
if errorlevel 2 (set ALAT=) else (set ALAT=--alat)
choice /C YN /M "Hapus juga DAFTAR KLIEN"
if errorlevel 2 (set KLIEN=) else (set KLIEN=--klien)
echo.

".venv\Scripts\python.exe" manage.py reset_data_uji %ALAT% %KLIEN%
echo.
pause
