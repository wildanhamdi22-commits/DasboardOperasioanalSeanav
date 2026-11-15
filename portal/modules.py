"""Konfigurasi 3 modul bisnis dan helper bersama (lampiran, pengguna, redirect)."""
import json
import uuid
from pathlib import Path

from django.core.files.storage import default_storage
from django.db import connection
from django.shortcuts import redirect
from django.urls import reverse

MODULES = {
    "RENTAL": {
        "code": "RENTAL", "label": "Rental", "kind_label": "Rental alat",
        "quotations": "rental_quotations", "quotation_create": "rental_quotation_create",
        "invoices": "rental_invoices",
        "quotation_hint": "Penawaran sewa alat: jumlah unit × durasi hari × tarif per hari.",
        "invoice_hint": "Tagihan sewa alat dari quotation rental yang disetujui klien.",
    },
    "PENJUALAN": {
        "code": "PENJUALAN", "label": "Penjualan", "kind_label": "Penjualan produk",
        "quotations": "sales_quotations", "quotation_create": "sales_quotation_create",
        "invoices": "sales_invoices",
        "quotation_hint": "Penawaran produk dari katalog: jumlah unit × harga per unit.",
        "invoice_hint": "Tagihan penjualan produk dari quotation penjualan yang disetujui.",
    },
    "JASA_SURVEY": {
        "code": "JASA_SURVEY", "label": "Jasa Survey", "kind_label": "Jasa survey",
        "quotations": "survey_quotations", "quotation_create": "survey_quotation_create",
        "invoices": "survey_invoices",
        "quotation_hint": "Proposal pekerjaan survei: mobilisasi, survei per hari, standby, olah data, laporan.",
        "invoice_hint": "Tagihan jasa survei per termin (DP, progres, pelunasan setelah BAST).",
    },
}

SURVEY_ITEM_PRESETS = [
    ("Mobilisasi peralatan & personel", "lot"),
    ("Survei lapangan (per hari)", "hari"),
    ("Standby cuaca (per hari)", "hari"),
    ("Pengolahan data", "lot"),
    ("Laporan & peta hasil survei", "lot"),
    ("Demobilisasi peralatan & personel", "lot"),
]

QC_CHECKLIST = [
    ("A. Identitas & dokumen", [
        ("sn_cocok", "Serial number di unit sama dengan packing list / invoice pabrikan"),
        ("garansi", "Kartu garansi tersedia"),
        ("sertifikat", "Sertifikat kalibrasi / factory test tersedia"),
        ("manual", "Manual / user guide tersedia"),
    ]),
    ("B. Fisik", [
        ("kemasan", "Kemasan & segel pabrik utuh"),
        ("cacat", "Tidak ada cacat, retak, atau goresan"),
        ("konektor", "Konektor & kabel dalam kondisi baik"),
    ]),
    ("C. Kelengkapan", [
        ("aksesori", "Aksesori lengkap sesuai packing list"),
        ("jumlah", "Jumlah item sesuai pesanan"),
    ]),
    ("D. Fungsi", [
        ("power", "Unit menyala normal"),
        ("selftest", "Self-test / diagnostic lulus"),
        ("uji_fungsi", "Uji fungsi singkat berhasil (fix posisi / data masuk / sensor membaca)"),
        ("baterai", "Baterai terisi & mengisi daya normal"),
    ]),
    ("E. Software & lisensi", [
        ("lisensi", "Lisensi / aktivasi software terpasang"),
    ]),
    ("F. Dokumentasi foto", [
        ("foto", "Foto unit, pelat SN, kelengkapan & kemasan sudah diambil"),
    ]),
]

RETURN_CHECKLIST = [
    ("lengkap", "Unit & aksesori kembali lengkap sesuai DO keluar"),
    ("fisik", "Fisik unit tanpa kerusakan baru"),
    ("fungsi", "Unit menyala & berfungsi normal"),
    ("bersih", "Unit dikembalikan dalam keadaan bersih / kering"),
]

ATTACHMENT_LABELS = {
    "DO_TTD": "DO bertanda tangan", "BA_KEMBALI": "Berita acara pengembalian", "LAINNYA": "Dokumen lain",
    "BAST": "Berita acara serah terima", "BAST_TTD": "BAST bertanda tangan", "FOTO_QC": "Foto QC",
    "FOTO": "Foto lapangan", "FOTO_KEMBALI": "Foto pengembalian", "FOTO_KELUAR": "Foto serah terima",
}

ATTACHMENT_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic", ".doc", ".docx",
                         ".xls", ".xlsx", ".zip", ".csv", ".txt", ".dwg", ".kml", ".kmz"}
ATTACHMENT_MAX = 25 * 1024 * 1024


def module(kind):
    return MODULES.get(kind or "RENTAL", MODULES["RENTAL"])


def module_redirect(kind, target="quotations"):
    return redirect(module(kind)[target])


def kind_of_quotation(quotation_id):
    with connection.cursor() as cursor:
        cursor.execute("SELECT jenis FROM quotation WHERE id = %s", [quotation_id])
        row = cursor.fetchone()
    return row[0] if row else "RENTAL"


def kind_of_invoice(invoice_id):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT q.jenis FROM invoice i JOIN quotation q ON q.id = i.quotation_id WHERE i.id = %s",
            [invoice_id],
        )
        row = cursor.fetchone()
    return row[0] if row else "RENTAL"


def validate_upload(upload):
    if not upload:
        return "Pilih file terlebih dahulu."
    if Path(upload.name).suffix.lower() not in ATTACHMENT_EXTENSIONS:
        return "Format file tidak didukung. Gunakan PDF, foto, dokumen Office, ZIP, atau KML."
    if upload.size > ATTACHMENT_MAX:
        return "Ukuran file maksimal 25 MB."
    return None


def save_attachment(cursor, request, entity, entity_id, kind, upload, title=None, note=None, user_id=None):
    suffix = Path(upload.name).suffix.lower()
    path = f"dokumen/{entity}/{entity_id}/{uuid.uuid4().hex[:12]}{suffix}"
    stored = default_storage.save(path, upload)
    cursor.execute(
        """
        INSERT INTO lampiran_dokumen (entitas, entitas_id, jenis, judul, file_path, nama_file, catatan, diunggah_oleh)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
        """,
        [entity, entity_id, kind, title or None, stored, upload.name[:255], note or None, user_id],
    )
    return cursor.fetchone()[0]


def attachments(cursor, entity, entity_ids, kinds=None):
    """Kembalikan dict entitas_id -> daftar lampiran."""
    if not entity_ids:
        return {}
    cursor.execute(
        """
        SELECT l.id, l.entitas_id, l.jenis, COALESCE(l.judul, ''), l.file_path, COALESCE(l.nama_file, ''),
               l.diunggah_pada, COALESCE(p.nama, ''), COALESCE(l.catatan, '')
        FROM lampiran_dokumen l
        LEFT JOIN pengguna p ON p.id = l.diunggah_oleh
        WHERE l.entitas = %s AND l.entitas_id = ANY(%s)
          AND (%s::text[] IS NULL OR l.jenis = ANY(%s::text[]))
        ORDER BY l.diunggah_pada DESC
        """,
        [entity, list(entity_ids), kinds, kinds],
    )
    result = {}
    for row in cursor.fetchall():
        is_image = Path(row[4]).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
        result.setdefault(row[1], []).append({
            "id": row[0], "kind": row[2], "kind_label": ATTACHMENT_LABELS.get(row[2], row[2].replace("_", " ").title()),
            "title": row[3] or row[5], "url": reverse("protected_media", args=[row[4]]),
            "name": row[5], "uploaded_at": row[6], "uploaded_by": row[7], "note": row[8], "is_image": is_image,
        })
    return result


def as_json(value):
    """Django memuat kolom jsonb sebagai teks; ubah ke dict/list."""
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None
