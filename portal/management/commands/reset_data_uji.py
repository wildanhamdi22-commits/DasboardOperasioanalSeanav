"""Hapus data uji coba agar aplikasi siap diisi dari awal.

Selalu dihapus : quotation, invoice, rental, serah terima, pengembalian, pesanan & QC penjualan,
                 proyek survey, laporan harian, deliverable, lampiran/arsip, audit, riwayat absensi
                 & izin/cuti, beserta file unggahannya.
Opsional       : --alat  (daftar alat rental & katalog produk)
                 --klien (daftar klien & kontak)
Tetap aman     : akun login, role & hak akses, data perusahaan, kategori alat, lokasi gudang.

Sebelum menghapus, seluruh isi tabel disalin ke folder backup_reset_<tanggal>/ (CSV per tabel)
dan file unggahan dipindahkan ke folder yang sama, sehingga data masih bisa dipulihkan.
"""
import csv
import shutil
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

TRANSACTIONS = [
    "qc_penjualan", "unit_penjualan", "serah_terima_penjualan_item", "serah_terima_penjualan",
    "pesanan_penjualan_item", "pesanan_penjualan",
    "serah_terima_rental_item", "serah_terima_rental", "rental_item", "rental",
    "laporan_harian", "deliverable_proyek", "proyek_personel", "proyek_unit", "proyek",
    "invoice", "quotation_persetujuan", "quotation_item", "quotation",
    "lampiran_dokumen", "audit_perubahan", "riwayat_unit", "jadwal_unit", "pergerakan_stok", "perawatan",
    "absensi", "pengajuan_izin",
]
EQUIPMENT = ["paket_unit_item", "paket_unit", "paket_alat_item", "paket_alat", "unit_alat", "model_alat", "produk_jual"]
CLIENTS = ["kontak_klien", "klien"]
MEDIA_DIRS = ["dokumen", "absensi", "izin"]
EQUIPMENT_MEDIA = ["inventory"]


class Command(BaseCommand):
    help = "Hapus data uji coba (transaksi & absensi) dengan backup otomatis."

    def add_arguments(self, parser):
        parser.add_argument("--alat", action="store_true", help="Ikut hapus daftar alat rental & katalog produk")
        parser.add_argument("--klien", action="store_true", help="Ikut hapus daftar klien & kontak")
        parser.add_argument("--ya", action="store_true", help="Lewati konfirmasi")

    def _existing(self, cursor, tables):
        out = []
        for t in tables:
            cursor.execute("SELECT to_regclass(%s)", [f"public.{t}"])
            if cursor.fetchone()[0]:
                out.append(t)
        return out

    def handle(self, *args, **opts):
        tables = list(TRANSACTIONS)
        if opts["alat"]:
            tables += EQUIPMENT
        if opts["klien"]:
            tables += CLIENTS
        with connection.cursor() as cursor:
            tables = self._existing(cursor, tables)
            counts = {}
            for t in tables:
                cursor.execute(f'SELECT COUNT(*) FROM "{t}"')
                counts[t] = cursor.fetchone()[0]
        total = sum(counts.values())
        self.stdout.write("\nData yang akan dihapus:")
        for t in tables:
            if counts[t]:
                self.stdout.write(f"  - {t:<30} {counts[t]:>6} baris")
        if not total:
            self.stdout.write(self.style.SUCCESS("Tidak ada data uji coba. Aplikasi sudah bersih."))
            return
        if not opts["ya"]:
            answer = input(f"\nKetik HAPUS untuk menghapus {total} baris data (backup dibuat otomatis): ").strip()
            if answer != "HAPUS":
                raise CommandError("Dibatalkan. Tidak ada data yang dihapus.")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = Path(settings.BASE_DIR) / f"backup_reset_{stamp}"
        backup.mkdir(parents=True, exist_ok=True)
        with connection.cursor() as cursor:
            for t in tables:
                cursor.execute(f'SELECT * FROM "{t}"')
                cols = [c[0] for c in cursor.description]
                with open(backup / f"{t}.csv", "w", newline="", encoding="utf-8") as fh:
                    writer = csv.writer(fh)
                    writer.writerow(cols)
                    writer.writerows(cursor.fetchall())
        self.stdout.write(f"Backup tabel tersimpan di {backup}")

        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE")
            if opts["alat"]:
                cursor.execute(
                    """
                    DELETE FROM kategori_alat k WHERE k.nama NOT IN ('Geodetic', 'Geophysic', 'Metocean')
                      AND NOT EXISTS (SELECT 1 FROM model_alat m WHERE m.kategori_alat_id = k.id)
                      AND NOT EXISTS (SELECT 1 FROM produk_jual p WHERE p.kategori_alat_id = k.id)
                    """
                )
            if not opts["alat"] and self._existing(cursor, ["unit_alat"]):
                cursor.execute("UPDATE unit_alat SET status = 'TERSEDIA' WHERE status IN ('DISEWA', 'DI_PROYEK')")

        media_root = Path(settings.MEDIA_ROOT)
        moved = []
        for folder in MEDIA_DIRS + (EQUIPMENT_MEDIA if opts["alat"] else []):
            src = media_root / folder
            if src.exists():
                shutil.move(str(src), str(backup / "media" / folder))
                moved.append(folder)
        if moved:
            self.stdout.write(f"File unggahan ({', '.join(moved)}) dipindahkan ke {backup / 'media'}")
        self.stdout.write(self.style.SUCCESS(f"\nSelesai. {total} baris data uji coba dihapus; nomor urut dimulai dari awal."))
        self.stdout.write("Akun login, role, data perusahaan, dan kategori alat tetap ada.")
