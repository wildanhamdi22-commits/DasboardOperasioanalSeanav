# Setup Seanav Operations di laptop baru: Python venv, paket, .env, dan database PostgreSQL.
$ErrorActionPreference = "Continue"
# Skrip ini ada di <repo>\scripts\windows\ ; folder aplikasi = dua tingkat di atas
$web = (Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..\..")).Path
$root = $web
Set-Location $web

function Step($t) { Write-Host ""; Write-Host "==> $t" -ForegroundColor Cyan }
function Fail($t) { Write-Host ""; Write-Host "[!] $t" -ForegroundColor Red; Read-Host "Tekan Enter untuk menutup"; exit 1 }

# 1. Python
Step "Memeriksa Python"
$pyExe = $null; $pyArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) { $pyExe = "py"; $pyArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $pyExe = "python" }
if (-not $pyExe) { Fail "Python belum terpasang. Pasang Python 3.12+ dari https://www.python.org/downloads/ (centang 'Add python.exe to PATH'), lalu jalankan file ini lagi." }
& $pyExe @pyArgs --version

# 2. Virtual environment (venv dari laptop lama tidak bisa dipakai)
Step "Menyiapkan virtual environment"
$venvPy = Join-Path $web ".venv\Scripts\python.exe"
$venvOk = $false
if (Test-Path $venvPy) {
  & $venvPy -c "import django, psycopg" *> $null
  if ($LASTEXITCODE -eq 0) { $venvOk = $true }
}
if (-not $venvOk) {
  if (Test-Path (Join-Path $web ".venv")) {
    $old = ".venv_laptop_lama_" + (Get-Date -Format "yyyyMMddHHmmss")
    Rename-Item (Join-Path $web ".venv") $old
    Write-Host "venv lama dipindah ke webapp\$old (boleh dihapus manual)."
  }
  & $pyExe @pyArgs -m venv .venv
  if ($LASTEXITCODE -ne 0) { Fail "Gagal membuat virtual environment." }
}
& $venvPy -m pip install --upgrade pip --quiet
& $venvPy -m pip install -r requirements.txt --quiet
if ($LASTEXITCODE -ne 0) { Fail "Gagal memasang paket Python. Periksa koneksi internet." }
Write-Host "Paket Python siap."

# 3. PostgreSQL (dipasang otomatis lewat winget bila belum ada)
Step "Mencari PostgreSQL"
function Find-Psql {
  $p = (Get-Command psql -ErrorAction SilentlyContinue).Source
  if (-not $p) {
    $c = Get-ChildItem "C:\Program Files\PostgreSQL\*\bin\psql.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
    if ($c) { $p = $c.FullName }
  }
  return $p
}
function Read-Pw($prompt) {
  $sec = Read-Host $prompt -AsSecureString
  return [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
$pw = $null
$psql = Find-Psql
if (-not $psql) {
  Write-Host "PostgreSQL belum terpasang di laptop ini." -ForegroundColor Yellow
  if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Fail "winget tidak tersedia. Pasang PostgreSQL 17 manual dari https://www.postgresql.org/download/windows/ , catat password user 'postgres', lalu jalankan file ini lagi."
  }
  $ans = Read-Host "Pasang PostgreSQL 17 otomatis sekarang? (Y/N)"
  if ($ans -notmatch '^[Yy]') { Fail "Dibatalkan. Pasang PostgreSQL lalu jalankan file ini lagi." }
  while ($true) {
    $pw = Read-Pw "Buat password untuk database (min. 8 karakter, huruf/angka saja)"
    $pw2 = Read-Pw "Ulangi password"
    if ($pw -ne $pw2) { Write-Host "Password tidak sama, ulangi." -ForegroundColor Yellow; continue }
    if ($pw -notmatch '^[A-Za-z0-9]{8,}$') { Write-Host "Gunakan minimal 8 karakter huruf/angka tanpa spasi atau simbol." -ForegroundColor Yellow; continue }
    break
  }
  Write-Host "Memasang PostgreSQL 17 (5-10 menit, Windows akan meminta izin Administrator)..."
  $override = "--mode unattended --unattendedmodeui minimal --superpassword $pw --serverport 5432 --disable-components stackbuilder"
  winget install --id PostgreSQL.PostgreSQL.17 --exact --silent --accept-package-agreements --accept-source-agreements --override $override
  $psql = Find-Psql
  if (-not $psql) { Fail "Pemasangan PostgreSQL belum berhasil. Coba jalankan file ini lagi, atau pasang manual dari https://www.postgresql.org/download/windows/" }
  Write-Host "PostgreSQL berhasil dipasang." -ForegroundColor Green
  Start-Sleep -Seconds 5
}
Write-Host "psql: $psql"

# 4. File .env
Step "Menyiapkan konfigurasi .env"
$envFile = Join-Path $web ".env"
if (-not (Test-Path $envFile)) { Copy-Item (Join-Path $web ".env.example") $envFile }
$lines = Get-Content $envFile
function EnvVal($k) { ($lines | Where-Object { $_ -match "^$k=" } | Select-Object -First 1) -replace "^$k=", "" }
$dbName = EnvVal "DB_NAME"; if (-not $dbName) { $dbName = "seanav_dev" }
$dbUser = EnvVal "DB_USER"; if (-not $dbUser) { $dbUser = "postgres" }
$dbHost = EnvVal "DB_HOST"; if (-not $dbHost) { $dbHost = "127.0.0.1" }
$dbPort = EnvVal "DB_PORT"; if (-not $dbPort) { $dbPort = "5432" }
if (-not $pw) { $pw = Read-Pw "Masukkan password PostgreSQL user '$dbUser' di laptop INI (tidak terlihat saat diketik)" }
$secret = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 50 | ForEach-Object { [char]$_ })
$out = @(); $seen = @{}
foreach ($l in $lines) {
  if ($l -match "^DB_PASSWORD=") { $out += "DB_PASSWORD=$pw"; $seen["pw"] = 1 }
  elseif ($l -match "^DJANGO_SECRET_KEY=(replace|local)") { $out += "DJANGO_SECRET_KEY=$secret"; $seen["sk"] = 1 }
  elseif ($l -match "^DJANGO_SECRET_KEY=") { $out += $l; $seen["sk"] = 1 }
  else { $out += $l }
}
if (-not $seen["pw"]) { $out += "DB_PASSWORD=$pw" }
if (-not $seen["sk"]) { $out += "DJANGO_SECRET_KEY=$secret" }
Set-Content -Path $envFile -Value $out -Encoding ASCII
$env:PGPASSWORD = $pw

& $psql -h $dbHost -p $dbPort -U $dbUser -d postgres -tAc "select 1" | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "Tidak bisa masuk ke PostgreSQL. Periksa password dan pastikan layanan PostgreSQL berjalan." }

# 5. Database
Step "Menyiapkan database $dbName"
$exists = & $psql -h $dbHost -p $dbPort -U $dbUser -d postgres -tAc "select 1 from pg_database where datname='$dbName'"
if (-not $exists) {
  & $psql -h $dbHost -p $dbPort -U $dbUser -d postgres -c "CREATE DATABASE $dbName ENCODING 'UTF8'" | Out-Null
  Write-Host "Database $dbName dibuat."
}
$hasTables = & $psql -h $dbHost -p $dbPort -U $dbUser -d $dbName -tAc "select count(*) from information_schema.tables where table_name='perusahaan'"
$backup = Join-Path $root "backup_seanav_dev.sql"
if ([int]$hasTables -eq 0) {
  if (Test-Path $backup) {
    Write-Host "Memulihkan data dari backup_seanav_dev.sql ..."
    & $psql -h $dbHost -p $dbPort -U $dbUser -d $dbName -v ON_ERROR_STOP=1 -q -f $backup
    if ($LASTEXITCODE -ne 0) { Fail "Gagal memulihkan backup." }
    Write-Host "Data dari laptop lama berhasil dipulihkan." -ForegroundColor Green
  } else {
    Write-Host "Backup tidak ditemukan - membuat database baru dari schema_awal.sql ..."
    & $psql -h $dbHost -p $dbPort -U $dbUser -d $dbName -v ON_ERROR_STOP=1 -q -f (Join-Path $root "database\schema_awal.sql")
    if ($LASTEXITCODE -ne 0) { Fail "Gagal membuat struktur database." }
    $seed = Join-Path $root "database\data_awal_dari_dokumen.sql"
    if (Test-Path $seed) {
      & $psql -h $dbHost -p $dbPort -U $dbUser -d $dbName -v ON_ERROR_STOP=1 -q -f $seed
      if ($LASTEXITCODE -eq 0) { Write-Host "Data master awal (klien, model alat, gudang) dimasukkan." -ForegroundColor Green }
    }
    Write-Host "Database baru siap. Unit alat & transaksi diisi lewat aplikasi." -ForegroundColor Green
  }
} else {
  Write-Host "Database sudah berisi tabel - tidak diubah."
}

# 6. Tabel Django
Step "Menyiapkan tabel akun Django"
& $venvPy manage.py migrate --noinput
if ($LASTEXITCODE -ne 0) { Fail "migrate gagal." }
& $venvPy manage.py check

Write-Host ""
Write-Host "=========================================================" -ForegroundColor Green
Write-Host " SELESAI. Laptop ini siap menjalankan Seanav Operations." -ForegroundColor Green
Write-Host "=========================================================" -ForegroundColor Green
Write-Host " Berikutnya:"
Write-Host "  - Kalau database baru/kosong: jalankan 'Buat Super Admin.bat'"
Write-Host "  - Jalankan 'Jalankan Seanav.bat' untuk membuka aplikasi"
Read-Host "Tekan Enter untuk menutup"
