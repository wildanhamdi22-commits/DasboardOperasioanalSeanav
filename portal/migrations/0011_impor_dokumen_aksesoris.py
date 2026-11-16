"""Impor alat & aksesoris dari dokumen kelengkapan (hasil review 'Review Daftar Alat & Aksesoris.xlsx').

Data dibaca dari portal/data_import/alat_impor.json (tidak disimpan di GitHub). Bila file tidak ada,
migrasi ini tidak melakukan apa-apa. Unit dengan S/N yang sudah ada tidak dibuat ulang; aksesorisnya
hanya ditambahkan bila unit itu belum punya daftar kelengkapan.
"""
import json
from pathlib import Path

from django.db import migrations

DATA = Path(__file__).resolve().parent.parent / "data_import" / "alat_impor.json"


def _id(cursor, sql, params, insert_sql, insert_params):
    cursor.execute(sql, params)
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(insert_sql, insert_params)
    return cursor.fetchone()[0]


def forwards(apps, schema_editor):
    if not DATA.exists():
        return
    units = json.loads(DATA.read_text(encoding="utf-8"))
    with schema_editor.connection.cursor() as cursor:
        for unit in units:
            category_id = _id(
                cursor, "SELECT id FROM kategori_alat WHERE nama = %s", [unit["kategori"]],
                "INSERT INTO kategori_alat (nama) VALUES (%s) RETURNING id", [unit["kategori"]],
            )
            brand = unit["merek"] or None
            model_id = _id(
                cursor,
                "SELECT id FROM model_alat WHERE kategori_alat_id = %s AND COALESCE(merek, '') = %s AND nama_model = %s",
                [category_id, unit["merek"] or "", unit["model"]],
                "INSERT INTO model_alat (kategori_alat_id, merek, nama_model) VALUES (%s, %s, %s) RETURNING id",
                [category_id, brand, unit["model"]],
            )
            serial = unit["sn"] or None
            unit_id = None
            if serial:
                cursor.execute(
                    "SELECT id FROM unit_alat WHERE nomor_seri = %s AND status <> 'DIARSIPKAN' ORDER BY id LIMIT 1",
                    [serial],
                )
                row = cursor.fetchone()
                unit_id = row[0] if row else None
            if unit_id is None:
                cursor.execute(
                    """
                    INSERT INTO unit_alat (model_alat_id, nomor_seri, kondisi, status, modul, catatan)
                    VALUES (%s, %s, 'BAIK', 'TERSEDIA', 'RENTAL', 'Diimpor dari dokumen kelengkapan alat')
                    RETURNING id
                    """,
                    [model_id, serial],
                )
                unit_id = cursor.fetchone()[0]
            if not unit["acc"]:
                continue
            cursor.execute("SELECT id, paket_alat_id FROM paket_unit WHERE unit_utama_id = %s", [unit_id])
            row = cursor.fetchone()
            if row:
                cursor.execute("SELECT COUNT(*) FROM paket_unit_item WHERE paket_unit_id = %s", [row[0]])
                if cursor.fetchone()[0]:
                    continue
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
            for acc in unit["acc"]:
                cursor.execute(
                    """
                    INSERT INTO paket_alat_item (paket_alat_id, nama_item, jumlah_standar, satuan, punya_serial, catatan)
                    VALUES (%s, %s, %s, 'unit', %s, %s) RETURNING id
                    """,
                    [package_id, acc["nama"][:180], acc["qty"] or 1, bool(acc["sn"]), acc["catatan"] or None],
                )
                item_id = cursor.fetchone()[0]
                cursor.execute(
                    """
                    INSERT INTO paket_unit_item (paket_unit_id, paket_alat_item_id, nomor_seri, jumlah, kondisi)
                    VALUES (%s, %s, %s, %s, 'BAIK')
                    """,
                    [package_unit_id, item_id, acc["sn"][:120] or None, acc["qty"] or 1],
                )


class Migration(migrations.Migration):
    dependencies = [("portal", "0010_batal_input_tidemaster")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
