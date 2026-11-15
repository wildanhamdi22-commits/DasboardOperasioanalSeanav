"""Siapkan database kosong: muat skema bisnis + data master, lalu (opsional) buat superuser.

Aman dijalankan berkali-kali: skema hanya dimuat bila tabel `perusahaan` belum ada.
Dipakai otomatis saat container/hosting dijalankan (scripts/start.sh).
"""
import os
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import connection

DB_DIR = Path(__file__).resolve().parents[3] / "database"


class Command(BaseCommand):
    help = "Muat schema_awal.sql & data_awal_dari_dokumen.sql bila database masih kosong."

    def add_arguments(self, parser):
        parser.add_argument("--tanpa-data-awal", action="store_true",
                            help="Jangan isi data master dari dokumen.")

    def handle(self, *args, **opts):
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.perusahaan') IS NOT NULL")
            exists = cursor.fetchone()[0]
        if exists:
            self.stdout.write("Skema bisnis sudah ada — tidak diubah.")
        else:
            self._run_sql(DB_DIR / "schema_awal.sql")
            self.stdout.write(self.style.SUCCESS("Skema bisnis dimuat dari schema_awal.sql."))
            if not opts["tanpa_data_awal"] and (DB_DIR / "data_awal_dari_dokumen.sql").is_file():
                self._run_sql(DB_DIR / "data_awal_dari_dokumen.sql")
                self.stdout.write(self.style.SUCCESS("Data master awal dimuat."))

        username = os.getenv("DJANGO_SUPERUSER_USERNAME")
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD")
        if username and password:
            from django.core.management import call_command
            call_command("migrate", interactive=False, verbosity=0)
            User = get_user_model()
            if not User.objects.filter(username=username).exists():
                User.objects.create_superuser(username=username, password=password,
                                              email=os.getenv("DJANGO_SUPERUSER_EMAIL", ""))
                self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' dibuat."))

    def _run_sql(self, path):
        sql = path.read_text(encoding="utf-8-sig")
        with connection.cursor() as cursor:
            cursor.execute(sql)
