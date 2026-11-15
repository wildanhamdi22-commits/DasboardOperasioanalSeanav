# Seanav Operations

Portal operasional internal **PT Oseana Prawira Persada** & **PT Seanav Maritim Indonesia** (Seanav Group):
inventaris alat survei, quotation, invoice, rental & serah terima, rekap proyek, serta absensi dan izin staf.

Dibangun dengan **Django 6 + PostgreSQL**. Siap dijalankan di komputer kantor (Windows) maupun di-hosting
(VPS dengan Docker, Render, atau Railway).

---

## Fitur

| Modul | Isi |
|---|---|
| **Website publik** | Landing page perusahaan (`/`) + halaman login staf |
| **Dashboard** | KPI pendapatan, piutang, utilisasi armada, rental, pipeline quotation, proyek; grafik tren pendapatan; peta sebaran operasi; peringatan (rental terlambat, invoice jatuh tempo, kalibrasi); aktivitas terbaru; filter periode; mode gelap; cetak |
| **Inventaris Alat** | Unit, serial number, OMSN, foto, aksesori, kondisi, status, riwayat keluar, arsip/pulihkan |
| **Quotation** | Buat penawaran multi-item (tarif/hari, PPN 11% otomatis), status draft → terkirim → disetujui/ditolak klien, unduh **Word & PDF** sesuai kop perusahaan |
| **Invoice** | Dibuat dari quotation yang disetujui, terbitkan, catat lunas, unduh Word & PDF |
| **Rental & Serah Terima** | Rental dari invoice (unit dipasangkan otomatis), alat keluar/kembali, status unit otomatis, lokasi penggunaan di peta |
| **Rekap Proyek** | Proyek, titik lokasi di peta, unit rental yang ditugaskan |
| **Absensi** | Absen masuk/pulang dengan **foto selfie** & **GPS** (deteksi di kantor/lapangan), status terlambat, kalender bulanan |
| **Izin & Cuti** | Pengajuan izin/sakit/cuti/dinas luar + lampiran, persetujuan atasan |
| **Rekap Absensi** | Rekap per staf per bulan (hadir, terlambat, izin, sakit, cuti, tanpa keterangan, jam kerja), detail harian dengan foto & lokasi, **ekspor Excel (CSV)** |
| **Akses & Role** | 5 role perusahaan (Direktur Utama, Manajer, Business Development, Business Support, Technical Sales) |
| **Profil** | Info akun & ganti kata sandi |

Hak akses: menu tampil sesuai role. Direktur Utama & Manajer dapat menyetujui izin dan melihat rekap absensi.
Superuser dapat mengakses semuanya.

---

## Struktur

```
├─ manage.py, requirements.txt, .env.example
├─ seanav_web/            pengaturan Django & URL
├─ portal/                aplikasi utama
│  ├─ views.py            inventaris, quotation, invoice, rental, proyek, dashboard
│  ├─ attendance.py       absensi, izin/cuti, rekap, profil, file media terlindungi
│  ├─ models.py           tabel absensi & pengajuan izin (migrasi Django)
│  ├─ pdf.py              konversi Word → PDF (Microsoft Word / LibreOffice)
│  ├─ static/portal/      app.css, app.js, logo
│  └─ management/commands/init_database.py
├─ templates/portal/      base.html (sidebar) + semua halaman
├─ document_templates/    template Word quotation & invoice (kop surat)
├─ database/
│  ├─ schema_awal.sql             skema bisnis lengkap
│  ├─ data_awal_dari_dokumen.sql  data master awal (klien, model alat, gudang)
│  └─ migrasi_sql/                riwayat migrasi SQL lama
├─ scripts/windows/       skrip klik-dua-kali untuk komputer kantor
├─ scripts/start.sh       start-up di server
├─ Dockerfile, docker-compose.yml, Caddyfile, render.yaml, Procfile
```

Database: tabel bisnis berasal dari `database/schema_awal.sql`, tabel login/absensi dari migrasi Django.
Perintah `python manage.py init_database` memuat skema & data awal **hanya bila database masih kosong**,
jadi aman dijalankan berulang.

---

## A. Menjalankan di komputer kantor (Windows)

1. Pasang **Python 3.12+** (centang *Add python.exe to PATH*). Untuk PDF, pasang **Microsoft Word**
   atau **LibreOffice**.
2. Buka folder `scripts\windows\`, klik dua kali **`1. Setup (sekali saja).bat`**:
   memasang PostgreSQL 17 otomatis bila belum ada, membuat virtual environment, mengisi `.env`,
   membuat database, dan memuat data awal.
3. Klik **`3. Buat Super Admin.bat`** → username `admin` + password acak tampil di layar (catat).
4. Klik **`2. Jalankan Seanav.bat`** → browser terbuka ke `http://<IP-komputer>:8000/`.
   Komputer lain di jaringan yang sama bisa membuka alamat yang tampil di jendela.

> Kamera & GPS di browser hanya aktif lewat **https://** atau **localhost**. Di jaringan kantor (http://IP),
> absensi tetap tersimpan tanpa foto/lokasi. Setelah di-hosting dengan HTTPS, foto & GPS aktif penuh.

Manual (tanpa skrip):

```bash
python -m venv .venv && .venv\Scripts\activate          # Linux/Mac: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env                                   # isi DB_PASSWORD, dll.
python manage.py init_database
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000
```

---

## B. Hosting

Variabel penting (lihat `.env.example`):

| Variabel | Contoh |
|---|---|
| `DJANGO_DEBUG` | `False` |
| `DJANGO_SECRET_KEY` | 50+ karakter acak |
| `DJANGO_ALLOWED_HOSTS` | `ops.seanavgroup.co.id` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://ops.seanavgroup.co.id` |
| `DATABASE_URL` | `postgres://user:pass@host:5432/seanav` |
| `DJANGO_SUPERUSER_USERNAME` / `_PASSWORD` | akun admin pertama (dibuat otomatis) |

Saat container start, `scripts/start.sh` menjalankan `init_database` → `migrate` → Gunicorn.
PDF di server dibuat dengan **LibreOffice** (sudah terpasang di Dockerfile).

### B0. Vercel + Supabase (gratis)

Ikuti **[PANDUAN_VERCEL.md](PANDUAN_VERCEL.md)**. Database & foto disimpan di Supabase; dokumen diunduh dalam format Word.

### B1. VPS (mis. Contabo) dengan Docker — direkomendasikan

```bash
git clone https://github.com/wildanhamdi22-commits/DasboardOperasioanalSeanav.git seanav
cd seanav
cp .env.example .env
# edit .env: DJANGO_DEBUG=False, DJANGO_SECRET_KEY, DJANGO_ALLOWED_HOSTS=<domain>,
#            DJANGO_CSRF_TRUSTED_ORIGINS=https://<domain>, DB_PASSWORD, DOMAIN=<domain>,
#            DJANGO_SUPERUSER_USERNAME/PASSWORD
docker compose up -d --build
```

Arahkan DNS domain ke IP VPS. Caddy membuat sertifikat HTTPS otomatis.
Data database & foto tersimpan di volume Docker (`pgdata`, `media`).

Backup harian (contoh cron):

```bash
docker compose exec -T db pg_dump -U seanav seanav > backup_$(date +%F).sql
```

### B2. Render

Dashboard Render → **New → Blueprint** → pilih repo ini. `render.yaml` membuat web service + PostgreSQL.
Isi `DJANGO_SUPERUSER_USERNAME/PASSWORD`, lalu tambahkan domain Anda ke `DJANGO_ALLOWED_HOSTS` dan
`DJANGO_CSRF_TRUSTED_ORIGINS`.

### B3. Railway

New Project → Deploy from GitHub repo → tambah **PostgreSQL** → di service web set
`DATABASE_URL=${{Postgres.DATABASE_URL}}`, `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY`, superuser.
Domain `*.up.railway.app` otomatis diizinkan. Tambahkan **Volume** ke `/app/media` agar foto tersimpan permanen.

---

## Pengaturan absensi

| Variabel | Default | Arti |
|---|---|---|
| `ABSENSI_JAM_MASUK` | `08:00` | jam masuk kantor |
| `ABSENSI_TOLERANSI_MENIT` | `15` | lewat dari ini = terlambat |
| `ABSENSI_WAJIB_FOTO` | `False` | `True` = absen wajib selfie |
| `KANTOR_LAT`, `KANTOR_LNG`, `KANTOR_RADIUS_M` | IDX Tower II, 200 m | penentu "di kantor" / "di luar kantor" |

Hari kerja dihitung Senin–Jumat. Pengajuan izin yang disetujui otomatis tercatat di rekap.

## Keamanan

- `.env`, folder `media/` (foto absensi, foto alat, lampiran), dan backup database **tidak** di-commit.
- Foto absensi & lampiran izin hanya bisa dibuka pemiliknya dan penyetuju (Direktur/Manajer/superuser).
- Mode produksi menolak berjalan tanpa `DJANGO_SECRET_KEY` yang kuat; cookie aman & HTTPS diaktifkan.
