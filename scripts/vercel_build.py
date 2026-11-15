"""Langkah build di Vercel: cek konfigurasi, siapkan database, kumpulkan file statis.

Dijalankan otomatis oleh Vercel (lihat vercel.json) setelah paket Python terpasang.
"""
import os
import subprocess
import sys

REQUIRED = {
    "DATABASE_URL": "URL PostgreSQL (Supabase/Neon). Contoh: postgres://user:pass@host:6543/postgres",
    "DJANGO_SECRET_KEY": "string acak minimal 50 karakter",
}
RECOMMENDED = {
    "S3_BUCKET": "penyimpanan foto absensi/alat (Supabase Storage / R2). Tanpa ini foto akan hilang.",
    "DJANGO_SUPERUSER_USERNAME": "akun admin pertama (dibuat otomatis)",
    "DJANGO_SUPERUSER_PASSWORD": "password akun admin pertama",
}


def run(*args):
    print("$ python manage.py", " ".join(args), flush=True)
    subprocess.run([sys.executable, "manage.py", *args], check=True)


def main():
    missing = [k for k in REQUIRED if not os.getenv(k)]
    if missing:
        print("\n" + "=" * 70)
        print("BUILD DIHENTIKAN: variabel lingkungan wajib belum diisi di Vercel")
        print("(Project → Settings → Environment Variables):")
        for key in missing:
            print(f"  - {key}: {REQUIRED[key]}")
        print("=" * 70 + "\n")
        sys.exit(1)
    for key, why in RECOMMENDED.items():
        if not os.getenv(key):
            print(f"PERINGATAN: {key} belum diisi — {why}")

    run("check", "--deploy", "--fail-level", "ERROR")
    run("init_database")
    run("migrate", "--noinput")
    run("collectstatic", "--noinput")
    print("Build Seanav Operations selesai.")


if __name__ == "__main__":
    main()
