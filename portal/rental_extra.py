"""Modul Rental: hapus alat, arsip DO, pengembalian alat, dan peta rental."""
import json
import logging

from django.contrib import messages
from django.db import DatabaseError, connection, transaction
from django.http import Http404
from django.shortcuts import redirect, render

from .modules import RETURN_CHECKLIST, as_json, attachments, save_attachment, validate_upload
from .views import _current_pengguna_id, business_permission_required

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- hapus alat
def _unit_usage(cursor, unit_id):
    checks = [
        ("rental", "SELECT COUNT(*) FROM rental_item WHERE unit_alat_id = %s"),
        ("serah terima", "SELECT COUNT(*) FROM serah_terima_rental_item WHERE unit_alat_id = %s"),
        ("proyek", "SELECT COUNT(*) FROM proyek_unit WHERE unit_alat_id = %s"),
        ("penjualan", "SELECT COUNT(*) FROM serah_terima_penjualan_item WHERE unit_alat_id = %s"),
        ("komponen paket alat lain", "SELECT COUNT(*) FROM paket_unit_item WHERE unit_alat_id = %s"),
    ]
    usage = []
    for label, sql in checks:
        cursor.execute(sql, [unit_id])
        count = cursor.fetchone()[0]
        if count:
            usage.append(f"{count} catatan {label}")
    return usage


@business_permission_required("INVENTARIS", "UBAH")
def inventory_delete(request, unit_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, concat_ws(' ', m.merek, m.nama_model), COALESCE(u.nomor_seri, '-'), k.nama,
                       u.status, u.model_alat_id
                FROM unit_alat u
                JOIN model_alat m ON m.id = u.model_alat_id
                JOIN kategori_alat k ON k.id = m.kategori_alat_id
                WHERE u.id = %s
                """,
                [unit_id],
            )
            unit = cursor.fetchone()
            if not unit:
                raise Http404("Alat tidak ditemukan.")
            usage = _unit_usage(cursor, unit_id)
    except DatabaseError:
        messages.error(request, "Data alat belum dapat dibaca.")
        return redirect("inventory")

    can_delete = not usage and unit[4] in ("TERSEDIA", "DIARSIPKAN")
    error = None
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        if len(reason) < 5:
            error = "Tuliskan alasan penghapusan (minimal 5 karakter)."
        else:
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        user_id = _current_pengguna_id(cursor, request)
                        usage = _unit_usage(cursor, unit_id)
                        snapshot = {"nama": unit[1], "nomor_seri": unit[2], "kategori": unit[3], "status": unit[4]}
                        if usage or unit[4] not in ("TERSEDIA", "DIARSIPKAN"):
                            cursor.execute("UPDATE unit_alat SET status = 'DIARSIPKAN' WHERE id = %s", [unit_id])
                            action, note = "UBAH_STATUS", "diarsipkan karena sudah memiliki riwayat transaksi"
                        else:
                            cursor.execute(
                                """
                                DELETE FROM paket_unit_item WHERE paket_unit_id IN
                                    (SELECT id FROM paket_unit WHERE unit_utama_id = %s)
                                """,
                                [unit_id],
                            )
                            cursor.execute("DELETE FROM paket_unit WHERE unit_utama_id = %s", [unit_id])
                            cursor.execute("DELETE FROM riwayat_unit WHERE unit_alat_id = %s", [unit_id])
                            cursor.execute("DELETE FROM jadwal_unit WHERE unit_alat_id = %s", [unit_id])
                            cursor.execute("DELETE FROM perawatan WHERE unit_alat_id = %s", [unit_id])
                            cursor.execute("DELETE FROM unit_alat WHERE id = %s", [unit_id])
                            cursor.execute(
                                """
                                DELETE FROM model_alat m WHERE m.id = %s
                                  AND NOT EXISTS (SELECT 1 FROM unit_alat u WHERE u.model_alat_id = m.id)
                                  AND NOT EXISTS (SELECT 1 FROM quotation_item qi WHERE qi.model_alat_id = m.id)
                                  AND NOT EXISTS (SELECT 1 FROM rental_item ri WHERE ri.model_alat_id = m.id)
                                  AND NOT EXISTS (SELECT 1 FROM paket_alat_item pai WHERE pai.model_alat_id = m.id)
                                """,
                                [unit[5]],
                            )
                            action, note = "HAPUS_LOGIS", "dihapus permanen"
                        cursor.execute(
                            """
                            INSERT INTO audit_perubahan (jenis_entitas, entitas_id, aksi, pengguna_id, alasan,
                                                         nilai_sebelum, nilai_sesudah, metadata)
                            VALUES ('unit_alat', %s, %s, %s, %s, %s::jsonb, NULL, %s::jsonb)
                            """,
                            [unit_id, action, user_id, reason, json.dumps(snapshot),
                             json.dumps({"django_user": request.user.get_username()})],
                        )
                messages.success(request, f"{unit[1]} (S/N {unit[2]}) {note}.")
                return redirect("inventory")
            except DatabaseError:
                logger.exception("Hapus unit %s gagal", unit_id)
                error = "Alat belum dapat dihapus karena masih dipakai data lain. Gunakan Arsipkan."
    return render(request, "portal/inventory_delete.html", {
        "unit": unit, "usage": usage, "can_delete": can_delete, "error": error,
    })


# --------------------------------------------------------------------------- arsip DO rental
@business_permission_required("RENTAL", "LIHAT")
def rental_archive(request, rental_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id, r.nomor_rental, p.kode, kl.nama_perusahaan, r.status,
                       r.tanggal_mulai_rencana, r.tanggal_selesai_rencana, COALESCE(r.lokasi_penggunaan, ''),
                       q.nomor, inv.nomor
                FROM rental r
                JOIN perusahaan p ON p.id = r.perusahaan_id
                JOIN klien kl ON kl.id = r.klien_id
                LEFT JOIN quotation q ON q.id = r.quotation_id
                LEFT JOIN invoice inv ON inv.id = r.invoice_id
                WHERE r.id = %s
                """,
                [rental_id],
            )
            rental = cursor.fetchone()
            if not rental:
                raise Http404("Rental tidak ditemukan.")
            if request.method == "POST":
                upload = request.FILES.get("file")
                problem = validate_upload(upload)
                if problem:
                    messages.error(request, problem)
                else:
                    with transaction.atomic():
                        user_id = _current_pengguna_id(cursor, request)
                        save_attachment(cursor, request, "rental", rental_id, request.POST.get("jenis") or "DO_TTD",
                                        upload, request.POST.get("title"), request.POST.get("note"), user_id)
                    messages.success(request, "Dokumen bertanda tangan tersimpan di arsip rental.")
                    return redirect("rental_archive", rental_id=rental_id)
            cursor.execute(
                """
                SELECT TRIM(concat_ws(' ', m.merek, m.nama_model)), COALESCE(u.nomor_seri, '-'), u.status,
                       ri.kondisi_keluar, ri.kondisi_kembali
                FROM rental_item ri
                LEFT JOIN unit_alat u ON u.id = ri.unit_alat_id
                LEFT JOIN model_alat m ON m.id = COALESCE(ri.model_alat_id, u.model_alat_id)
                WHERE ri.rental_id = %s ORDER BY ri.id
                """,
                [rental_id],
            )
            units = cursor.fetchall()
            cursor.execute(
                """
                SELECT s.id, s.jenis, s.tanggal, COALESCE(s.penerima_nama, ''), COALESCE(p.nama, ''),
                       COALESCE(s.catatan, ''), s.checklist
                FROM serah_terima_rental s LEFT JOIN pengguna p ON p.id = s.pic_internal_id
                WHERE s.rental_id = %s ORDER BY s.tanggal
                """,
                [rental_id],
            )
            handovers = [{"id": r[0], "kind": r[1], "date": r[2], "recipient": r[3], "pic": r[4], "notes": r[5],
                          "checklist": as_json(r[6])} for r in cursor.fetchall()]
            files = attachments(cursor, "rental", [rental_id]).get(rental_id, [])
            photos = attachments(cursor, "serah_terima_rental", [h["id"] for h in handovers])
            for h in handovers:
                h["photos"] = photos.get(h["id"], [])
    except DatabaseError:
        messages.error(request, "Arsip rental belum dapat dibaca.")
        return redirect("rental_list")
    return render(request, "portal/rental_archive.html", {
        "rental": rental, "units": units, "handovers": handovers, "files": files,
        "signed": [f for f in files if f["kind"] == "DO_TTD"],
        "return_labels": dict(RETURN_CHECKLIST),
    })


# --------------------------------------------------------------------------- pengembalian
@business_permission_required("RENTAL", "LIHAT")
def rental_returns(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id, r.nomor_rental, p.kode, kl.nama_perusahaan, r.tanggal_selesai_rencana,
                       COUNT(u.id) FILTER (WHERE u.status = 'DISEWA'),
                       string_agg(concat_ws(' ', m.merek, m.nama_model, 'S/N ' || u.nomor_seri), ', ')
                           FILTER (WHERE u.status = 'DISEWA'),
                       r.tanggal_selesai_rencana < CURRENT_DATE
                FROM rental r
                JOIN perusahaan p ON p.id = r.perusahaan_id
                JOIN klien kl ON kl.id = r.klien_id
                JOIN rental_item ri ON ri.rental_id = r.id
                LEFT JOIN unit_alat u ON u.id = ri.unit_alat_id
                LEFT JOIN model_alat m ON m.id = COALESCE(ri.model_alat_id, u.model_alat_id)
                WHERE r.status = 'BERJALAN'
                GROUP BY r.id, p.kode, kl.nama_perusahaan
                ORDER BY r.tanggal_selesai_rencana
                """
            )
            outstanding = [{"id": r[0], "number": r[1], "company": r[2], "client": r[3], "due": r[4],
                            "units_out": r[5], "units": r[6], "overdue": r[7]} for r in cursor.fetchall()]
            cursor.execute(
                """
                SELECT s.id, r.id, r.nomor_rental, kl.nama_perusahaan, s.tanggal, COALESCE(s.penerima_nama, ''),
                       COALESCE(pg.nama, ''), s.checklist, COALESCE(s.catatan, ''),
                       string_agg(concat_ws(' ', m.merek, m.nama_model, 'S/N ' || u.nomor_seri)
                                  || ' · ' || COALESCE(si.kondisi, '-'), ', ')
                FROM serah_terima_rental s
                JOIN rental r ON r.id = s.rental_id
                JOIN klien kl ON kl.id = r.klien_id
                LEFT JOIN pengguna pg ON pg.id = s.pic_internal_id
                LEFT JOIN serah_terima_rental_item si ON si.serah_terima_id = s.id
                LEFT JOIN unit_alat u ON u.id = si.unit_alat_id
                LEFT JOIN model_alat m ON m.id = u.model_alat_id
                WHERE s.jenis = 'KEMBALI'
                GROUP BY s.id, r.id, kl.nama_perusahaan, pg.nama
                ORDER BY s.tanggal DESC LIMIT 200
                """
            )
            history = [{"id": r[0], "rental_id": r[1], "number": r[2], "client": r[3], "date": r[4],
                        "returned_by": r[5], "pic": r[6], "checklist": as_json(r[7]) or {}, "notes": r[8], "units": r[9]}
                       for r in cursor.fetchall()]
            photos = attachments(cursor, "serah_terima_rental", [h["id"] for h in history])
            for h in history:
                h["photos"] = photos.get(h["id"], [])
                h["checked"] = sum(1 for v in h["checklist"].values() if v)
                h["total_checks"] = len(h["checklist"])
    except DatabaseError:
        outstanding, history = [], []
        messages.error(request, "Data pengembalian belum dapat dibaca.")
    return render(request, "portal/rental_returns.html", {"outstanding": outstanding, "history": history})


# --------------------------------------------------------------------------- peta
@business_permission_required("RENTAL", "LIHAT")
def rental_map(request):
    points = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id, r.nomor_rental, kl.nama_perusahaan, r.status, COALESCE(r.lokasi_penggunaan, ''),
                       r.latitude, r.longitude, r.tanggal_mulai_rencana, r.tanggal_selesai_rencana,
                       string_agg(DISTINCT concat_ws(' ', m.merek, m.nama_model), ', '), p.kode
                FROM rental r
                JOIN klien kl ON kl.id = r.klien_id
                JOIN perusahaan p ON p.id = r.perusahaan_id
                LEFT JOIN rental_item ri ON ri.rental_id = r.id
                LEFT JOIN unit_alat u ON u.id = ri.unit_alat_id
                LEFT JOIN model_alat m ON m.id = COALESCE(ri.model_alat_id, u.model_alat_id)
                WHERE r.status <> 'DIBATALKAN'
                GROUP BY r.id, kl.nama_perusahaan, p.kode
                ORDER BY r.tanggal_mulai_rencana DESC
                """
            )
            for r in cursor.fetchall():
                points.append({"id": r[0], "title": r[1], "client": r[2], "status": r[3], "location": r[4],
                               "lat": float(r[5]) if r[5] is not None else None,
                               "lng": float(r[6]) if r[6] is not None else None,
                               "period": f"{r[7]:%d %b %Y} – {r[8]:%d %b %Y}", "detail": r[9] or "", "company": r[10],
                               "url": f"/rental/{r[0]}/arsip/"})
    except DatabaseError:
        messages.error(request, "Data peta rental belum dapat dibaca.")
    return render(request, "portal/module_map.html", {
        "title": "Peta Rental", "crumb": "Rental · Peta Rental",
        "intro": "Lokasi penggunaan alat sewa — berjalan dan selesai.",
        "points": points,
        "legend": [("BERJALAN", "Rental berjalan", "#c8102e"), ("SELESAI", "Rental selesai", "#16a3c4"),
                   ("DIPROSES", "Diproses", "#a9682b")],
        "missing_hint": "Tandai lokasi dari menu Catatan Rental → Lokasi.",
    })
