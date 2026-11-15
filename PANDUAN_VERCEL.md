# Panduan Hosting Gratis: Vercel + Supabase

Vercel menjalankan aplikasinya, Supabase menyediakan **database PostgreSQL** dan **penyimpanan foto** (gratis).

> Di Vercel, dokumen quotation/invoice diunduh dalam format **Word (DOCX)** — Vercel tidak menyediakan
> Microsoft Word/LibreOffice untuk membuat PDF. Buka DOCX lalu *Save as PDF* bila perlu.
> Paket gratis Supabase: database 500 MB, file 1 GB, dan proyek dijeda bila 7 hari tidak dipakai sama sekali.

## 1. Supabase — database & penyimpanan

1. Daftar di https://supabase.com → **New project**.
   - Name: `seanav`, Region: **Southeast Asia (Singapore)**.
   - Buat **Database password** yang kuat dan simpan.
2. **Database URL**: klik **Connect** (atas) → tab **Connection string** → pilih **Transaction pooler** (port `6543`).
   Salin URI-nya dan ganti `[YOUR-PASSWORD]` dengan password tadi. Contoh:
   `postgresql://postgres.abcdefgh:PASSWORD@aws-0-ap-southeast-1.pooler.supabase.com:6543/postgres`
3. **Penyimpanan foto**:
   - Menu **Storage** → **New bucket** → nama `seanav`, biarkan **Public = OFF** → Create.
   - **Storage → Settings (S3 Configuration)** → aktifkan *S3 connection*. Catat **Endpoint** dan **Region**.
   - Di bagian **S3 Access Keys** → **New access key** → catat **Access key ID** dan **Secret access key**.

## 2. Vercel — variabel lingkungan

Buka proyek di Vercel → **Settings → Environment Variables**, tambahkan (Environment: *Production* & *Preview*):

| Nama | Isi |
|---|---|
| `DJANGO_SECRET_KEY` | string acak ≥ 50 karakter (mis. dari https://djecrety.ir) |
| `DATABASE_URL` | URI Transaction pooler dari langkah 1.2 |
| `S3_BUCKET` | `seanav` |
| `S3_ENDPOINT_URL` | Endpoint dari langkah 1.3 (diakhiri `/storage/v1/s3`) |
| `S3_REGION` | Region dari langkah 1.3 (mis. `ap-southeast-1`) |
| `S3_ACCESS_KEY_ID` | Access key ID |
| `S3_SECRET_ACCESS_KEY` | Secret access key |
| `DJANGO_SUPERUSER_USERNAME` | `admin` |
| `DJANGO_SUPERUSER_PASSWORD` | password admin pertama (kuat) |

Opsional: `DJANGO_ALLOWED_HOSTS` & `DJANGO_CSRF_TRUSTED_ORIGINS` bila memakai domain sendiri
(mis. `ops.seanavgroup.co.id` dan `https://ops.seanavgroup.co.id`). Domain `*.vercel.app` otomatis diizinkan.
Pengaturan absensi (`ABSENSI_JAM_MASUK`, `KANTOR_LAT`, dll.) sama seperti di `.env.example`.

## 3. Deploy ulang

**Deployments** → titik tiga pada deployment terakhir → **Redeploy**.
Saat build, aplikasi otomatis: memeriksa variabel, membuat tabel & data awal, membuat akun admin, dan menyiapkan file statis.
Bila ada variabel wajib yang kosong, build berhenti dengan pesan yang menyebutkan nama variabelnya (lihat *Build Logs*).

Setelah selesai, buka `https://<nama-proyek>.vercel.app/login/` dan masuk dengan akun admin.
Kamera & GPS absensi langsung aktif karena Vercel memakai HTTPS.
