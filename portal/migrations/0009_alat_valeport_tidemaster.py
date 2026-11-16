"""Input alat: Valeport TideMaster (Pressure), S/N 54593, beserta kelengkapan & fotonya.

Hanya berjalan bila file fotonya ada di folder media (yaitu di laptop kantor), sehingga
instalasi lain tidak mendapat data dengan foto yang hilang. Aman dijalankan ulang.
"""
from django.conf import settings
from django.db import migrations

FOLDER = "inventory/valeport-tidemaster-54593"
SERIAL = "54593"
ACCESSORIES = [
    ("Sensor tekanan (pressure transducer) + kabel", 1, "sensor_kabel.jpg"),
    ("Kabel komunikasi & power (DB9 + banana plug)", 1, "kabel_komunikasi.jpg"),
    ("Mounting bracket stainless", 1, "bracket.jpg"),
    ("Kotak hitam (box aksesori)", 1, "kotak_hitam.jpg"),
    ("Hard case biru", 1, "hardcase.jpg"),
]


def forwards(apps, schema_editor):
    from pathlib import Path

    media = Path(settings.MEDIA_ROOT) / FOLDER
    if not (media / "unit.jpg").exists():
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.paket_unit_item')")
        if not cursor.fetchone()[0]:
            return
        cursor.execute("SELECT id FROM kategori_alat WHERE nama = 'Metocean'")
        row = cursor.fetchone()
        if row:
            category_id = row[0]
        else:
            cursor.execute("INSERT INTO kategori_alat (nama) VALUES ('Metocean') RETURNING id")
            category_id = cursor.fetchone()[0]
        cursor.execute(
            "SELECT id FROM model_alat WHERE kategori_alat_id = %s AND merek = 'Valeport' AND nama_model = 'TideMaster'",
            [category_id],
        )
        row = cursor.fetchone()
        if row:
            model_id = row[0]
        else:
            cursor.execute(
                """
                INSERT INTO model_alat (kategori_alat_id, merek, nama_model, deskripsi)
                VALUES (%s, 'Valeport', 'TideMaster', 'Tide gauge (pressure) - Valeport TideMaster / Teledyne')
                RETURNING id
                """,
                [category_id],
            )
            model_id = cursor.fetchone()[0]

        cursor.execute("SELECT id, foto_url FROM unit_alat WHERE nomor_seri = %s ORDER BY id LIMIT 1", [SERIAL])
        row = cursor.fetchone()
        if row:
            unit_id = row[0]
            if not row[1]:
                cursor.execute("UPDATE unit_alat SET foto_url = %s WHERE id = %s", [f"{FOLDER}/unit.jpg", unit_id])
        else:
            cursor.execute(
                """
                INSERT INTO unit_alat (model_alat_id, nomor_seri, foto_url, kondisi, status, modul, catatan)
                VALUES (%s, %s, %s, 'BAIK', 'TERSEDIA', 'RENTAL', 'Tide Master Pressure Valeport (Teledyne)')
                RETURNING id
                """,
                [model_id, SERIAL, f"{FOLDER}/unit.jpg"],
            )
            unit_id = cursor.fetchone()[0]

        cursor.execute("SELECT id, paket_alat_id FROM paket_unit WHERE unit_utama_id = %s", [unit_id])
        row = cursor.fetchone()
        if row:
            cursor.execute("SELECT COUNT(*) FROM paket_unit_item WHERE paket_unit_id = %s", [row[0]])
            if cursor.fetchone()[0]:
                return  # kelengkapan sudah dicatat manual; jangan ditimpa
            package_unit_id, package_id = row
        else:
            cursor.execute(
                "INSERT INTO paket_alat (nama, keterangan) VALUES (%s, %s) RETURNING id",
                [f"Kelengkapan unit {unit_id}", "Daftar aksesori untuk satu unit inventaris."],
            )
            package_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO paket_unit (paket_alat_id, unit_utama_id) VALUES (%s, %s) RETURNING id",
                [package_id, unit_id],
            )
            package_unit_id = cursor.fetchone()[0]
        for name, qty, photo in ACCESSORIES:
            cursor.execute(
                "INSERT INTO paket_alat_item (paket_alat_id, nama_item, jumlah_standar, satuan) VALUES (%s, %s, %s, 'unit') RETURNING id",
                [package_id, name, qty],
            )
            item_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO paket_unit_item (paket_unit_id, paket_alat_item_id, jumlah, kondisi, foto_url)
                VALUES (%s, %s, %s, 'BAIK', %s)
                """,
                [package_unit_id, item_id, qty, f"{FOLDER}/{photo}" if (media / photo).exists() else None],
            )


class Migration(migrations.Migration):
    dependencies = [("portal", "0008_foto_aksesori")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
