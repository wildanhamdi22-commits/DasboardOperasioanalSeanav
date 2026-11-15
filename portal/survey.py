"""Modul Jasa Survey: detail proyek, laporan harian (DPR), deliverable & BAST, peta proyek."""
import logging
from decimal import Decimal, InvalidOperation

from django.contrib import messages
from django.db import DatabaseError, connection, transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone

from .modules import attachments, save_attachment, validate_upload
from .views import _current_pengguna_id, business_permission_required

logger = logging.getLogger(__name__)

PROJECT_STATUSES = [("DIRENCANAKAN", "Direncanakan"), ("MOBILISASI", "Mobilisasi"), ("BERJALAN", "Berjalan"),
                    ("PENGOLAHAN_DATA", "Pengolahan data"), ("SELESAI", "Selesai")]


def _num(value, default=None):
    try:
        return Decimal(str(value)) if value not in (None, "") else default
    except InvalidOperation:
        return default


def _project(cursor, project_id):
    cursor.execute(
        """
        SELECT pr.id, pr.kode_proyek, pr.nama, kl.nama_perusahaan, co.kode, pr.jenis_layanan,
               COALESCE(pr.lokasi_teks, ''), pr.latitude, pr.longitude, pr.tanggal_mulai_rencana,
               pr.tanggal_selesai_rencana, pr.status, COALESCE(pr.kru, ''), COALESCE(pr.kapal, ''),
               COALESCE(pr.progres, 0), pr.bast_tanggal, COALESCE(pr.bast_penerima, ''), q.id, q.nomor, q.total,
               COALESCE(pr.catatan, '')
        FROM proyek pr
        JOIN klien kl ON kl.id = pr.klien_id
        JOIN perusahaan co ON co.id = pr.perusahaan_id
        LEFT JOIN quotation q ON q.id = pr.quotation_id
        WHERE pr.id = %s
        """,
        [project_id],
    )
    r = cursor.fetchone()
    if not r:
        raise Http404("Proyek tidak ditemukan.")
    return dict(zip(["id", "code", "name", "client", "company", "service", "location", "lat", "lng", "start", "end",
                     "status", "crew", "vessel", "progress", "bast_date", "bast_receiver", "quotation_id",
                     "quotation", "contract", "notes"], r))



def _release_units(cursor, project_id):
    cursor.execute(
        """
        UPDATE unit_alat SET status = 'TERSEDIA'
        WHERE status = 'DI_PROYEK' AND id IN (SELECT unit_alat_id FROM proyek_unit WHERE proyek_id = %s)
        """,
        [project_id],
    )

@business_permission_required("PROYEK", "LIHAT")
def survey_project_detail(request, project_id):
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                user_id = _current_pengguna_id(cursor, request)
                if action == "update":
                    status = request.POST.get("status")
                    if status not in dict(PROJECT_STATUSES):
                        status = "BERJALAN"
                    progress = min(max(_num(request.POST.get("progress"), Decimal(0)), Decimal(0)), Decimal(100))
                    cursor.execute(
                        """
                        UPDATE proyek SET status = %s, progres = %s, kru = NULLIF(%s, ''), kapal = NULLIF(%s, ''),
                               tanggal_mulai_aktual = CASE WHEN %s IN ('MOBILISASI', 'BERJALAN')
                                    THEN COALESCE(tanggal_mulai_aktual, CURRENT_DATE) ELSE tanggal_mulai_aktual END,
                               tanggal_selesai_aktual = CASE WHEN %s = 'SELESAI'
                                    THEN COALESCE(tanggal_selesai_aktual, CURRENT_DATE) ELSE tanggal_selesai_aktual END
                        WHERE id = %s
                        """,
                        [status, progress, request.POST.get("crew", "").strip(), request.POST.get("vessel", "").strip()[:200],
                         status, status, project_id],
                    )
                    if status == "SELESAI":
                        _release_units(cursor, project_id)
                    messages.success(request, "Status, progres, kru, dan kapal proyek diperbarui.")
                elif action == "dpr":
                    activity = request.POST.get("activity", "").strip()
                    day = request.POST.get("date") or timezone.localdate().isoformat()
                    if len(activity) < 5:
                        messages.error(request, "Tuliskan aktivitas hari ini (minimal 5 karakter).")
                    else:
                        progress = _num(request.POST.get("progress"))
                        cursor.execute(
                            """
                            INSERT INTO laporan_harian (proyek_id, tanggal, aktivitas, cuaca, jam_kerja, jam_standby,
                                                        progres, kendala, dibuat_oleh)
                            VALUES (%s, %s, %s, NULLIF(%s, ''), %s, %s, %s, NULLIF(%s, ''), %s)
                            ON CONFLICT (proyek_id, tanggal) DO UPDATE SET aktivitas = EXCLUDED.aktivitas,
                                cuaca = EXCLUDED.cuaca, jam_kerja = EXCLUDED.jam_kerja, jam_standby = EXCLUDED.jam_standby,
                                progres = EXCLUDED.progres, kendala = EXCLUDED.kendala
                            RETURNING id
                            """,
                            [project_id, day, activity, request.POST.get("weather", "").strip()[:120],
                             _num(request.POST.get("work_hours"), Decimal(0)),
                             _num(request.POST.get("standby_hours"), Decimal(0)), progress,
                             request.POST.get("issues", "").strip(), user_id],
                        )
                        report_id = cursor.fetchone()[0]
                        if progress is not None:
                            cursor.execute("UPDATE proyek SET progres = %s WHERE id = %s",
                                           [min(max(progress, Decimal(0)), Decimal(100)), project_id])
                        for photo in request.FILES.getlist("photos")[:10]:
                            if validate_upload(photo) is None:
                                save_attachment(cursor, request, "laporan_harian", report_id, "FOTO", photo, user_id=user_id)
                        messages.success(request, f"Laporan harian {day} tersimpan.")
                elif action == "deliverable":
                    title = request.POST.get("title", "").strip()
                    upload = request.FILES.get("file")
                    problem = validate_upload(upload)
                    if not title:
                        messages.error(request, "Isi judul deliverable.")
                    elif problem:
                        messages.error(request, problem)
                    else:
                        from django.core.files.storage import default_storage
                        import uuid
                        from pathlib import Path
                        path = default_storage.save(
                            f"dokumen/proyek/{project_id}/{uuid.uuid4().hex[:12]}{Path(upload.name).suffix.lower()}", upload)
                        cursor.execute(
                            """
                            INSERT INTO deliverable_proyek (proyek_id, judul, versi, file_path, nama_file, catatan, diunggah_oleh)
                            VALUES (%s, %s, %s, %s, %s, NULLIF(%s, ''), %s)
                            """,
                            [project_id, title[:200], "FINAL" if request.POST.get("version") == "FINAL" else "DRAFT",
                             path, upload.name[:255], request.POST.get("note", "").strip(), user_id],
                        )
                        messages.success(request, "Deliverable tersimpan.")
                elif action == "bast":
                    receiver = request.POST.get("bast_receiver", "").strip()
                    if not receiver:
                        messages.error(request, "Isi nama penerima BAST di pihak klien.")
                    else:
                        cursor.execute(
                            """
                            UPDATE proyek SET bast_tanggal = %s, bast_penerima = %s, status = 'SELESAI', progres = 100,
                                   tanggal_selesai_aktual = COALESCE(tanggal_selesai_aktual, %s)
                            WHERE id = %s
                            """,
                            [request.POST.get("bast_date") or timezone.localdate(), receiver[:180],
                             request.POST.get("bast_date") or timezone.localdate(), project_id],
                        )
                        _release_units(cursor, project_id)
                        upload = request.FILES.get("file")
                        if upload and validate_upload(upload) is None:
                            save_attachment(cursor, request, "proyek", project_id, "BAST_TTD", upload,
                                            "BAST bertanda tangan", None, user_id)
                        messages.success(request, "BAST dicatat dan proyek ditandai selesai. Invoice pelunasan dapat dibuat.")
        except DatabaseError:
            logger.exception("Aksi proyek %s gagal", project_id)
            messages.error(request, "Perubahan belum tersimpan. Jalankan migrasi database bila baru memperbarui aplikasi.")
        return redirect("survey_project_detail", project_id=project_id)

    try:
        with connection.cursor() as cursor:
            project = _project(cursor, project_id)
            cursor.execute(
                """
                SELECT TRIM(concat_ws(' ', m.merek, m.nama_model)), COALESCE(u.nomor_seri, '-'), k.nama, u.status
                FROM proyek_unit pu JOIN unit_alat u ON u.id = pu.unit_alat_id
                JOIN model_alat m ON m.id = u.model_alat_id JOIN kategori_alat k ON k.id = m.kategori_alat_id
                WHERE pu.proyek_id = %s ORDER BY k.nama, m.nama_model
                """,
                [project_id],
            )
            equipment = cursor.fetchall()
            cursor.execute(
                """
                SELECT l.id, l.tanggal, l.aktivitas, COALESCE(l.cuaca, ''), l.jam_kerja, l.jam_standby, l.progres,
                       COALESCE(l.kendala, ''), COALESCE(p.nama, '')
                FROM laporan_harian l LEFT JOIN pengguna p ON p.id = l.dibuat_oleh
                WHERE l.proyek_id = %s ORDER BY l.tanggal DESC
                """,
                [project_id],
            )
            reports = [{"id": r[0], "date": r[1], "activity": r[2], "weather": r[3], "work": r[4], "standby": r[5],
                        "progress": r[6], "issues": r[7], "by": r[8]} for r in cursor.fetchall()]
            photos = attachments(cursor, "laporan_harian", [r["id"] for r in reports])
            for r in reports:
                r["photos"] = photos.get(r["id"], [])
            cursor.execute(
                """
                SELECT d.id, d.judul, d.versi, d.file_path, COALESCE(d.nama_file, ''), d.diunggah_pada,
                       COALESCE(p.nama, ''), COALESCE(d.catatan, '')
                FROM deliverable_proyek d LEFT JOIN pengguna p ON p.id = d.diunggah_oleh
                WHERE d.proyek_id = %s ORDER BY d.diunggah_pada DESC
                """,
                [project_id],
            )
            from django.urls import reverse
            deliverables = [{"id": r[0], "title": r[1], "version": r[2], "url": reverse("protected_media", args=[r[3]]) if r[3] else "",
                             "name": r[4], "at": r[5], "by": r[6], "note": r[7]} for r in cursor.fetchall()]
            cursor.execute(
                """
                SELECT i.id, i.nomor, i.termin_ke, i.persen_termin, COALESCE(i.keterangan_termin, ''), i.total, i.status
                FROM invoice i WHERE i.quotation_id = %s AND i.status <> 'DIBATALKAN' ORDER BY i.termin_ke NULLS FIRST, i.id
                """,
                [project["quotation_id"] or 0],
            )
            invoices = [{"id": r[0], "number": r[1], "termin": r[2], "percent": r[3], "label": r[4], "total": r[5],
                         "status": r[6]} for r in cursor.fetchall()]
            files = attachments(cursor, "proyek", [project_id]).get(project_id, [])
    except DatabaseError:
        messages.error(request, "Data proyek belum dapat dibaca. Jalankan migrasi database.")
        return redirect("project_list")

    totals = {
        "work": sum((r["work"] or 0) for r in reports), "standby": sum((r["standby"] or 0) for r in reports),
        "days": len(reports), "billed": sum((i["percent"] or 0) for i in invoices),
        "billed_amount": sum((i["total"] or 0) for i in invoices),
    }
    return render(request, "portal/survey_project_detail.html", {
        "p": project, "equipment": equipment, "reports": reports, "deliverables": deliverables, "invoices": invoices,
        "files": files, "statuses": PROJECT_STATUSES, "totals": totals, "today": timezone.localdate().isoformat(),
        "final_count": sum(1 for d in deliverables if d["version"] == "FINAL"),
    })


@business_permission_required("PROYEK", "LIHAT")
def survey_reports(request):
    rows = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT l.id, l.tanggal, pr.id, pr.kode_proyek, pr.nama, kl.nama_perusahaan, l.aktivitas,
                       COALESCE(l.cuaca, ''), l.jam_kerja, l.jam_standby, l.progres, COALESCE(l.kendala, ''),
                       COALESCE(p.nama, '')
                FROM laporan_harian l
                JOIN proyek pr ON pr.id = l.proyek_id
                JOIN klien kl ON kl.id = pr.klien_id
                LEFT JOIN pengguna p ON p.id = l.dibuat_oleh
                ORDER BY l.tanggal DESC, l.id DESC LIMIT 300
                """
            )
            rows = [{"id": r[0], "date": r[1], "project_id": r[2], "code": r[3], "name": r[4], "client": r[5],
                     "activity": r[6], "weather": r[7], "work": r[8], "standby": r[9], "progress": r[10],
                     "issues": r[11], "by": r[12]} for r in cursor.fetchall()]
            cursor.execute(
                "SELECT id, kode_proyek, nama FROM proyek WHERE status NOT IN ('SELESAI', 'DIARSIPKAN', 'DIBATALKAN') ORDER BY kode_proyek"
            )
            active = cursor.fetchall()
    except DatabaseError:
        active = []
        messages.error(request, "Laporan harian belum dapat dibaca. Jalankan migrasi database.")
    return render(request, "portal/survey_reports.html", {"rows": rows, "active": active})


@business_permission_required("PROYEK", "LIHAT")
def survey_deliverables(request):
    rows = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pr.id, pr.kode_proyek, pr.nama, kl.nama_perusahaan, pr.status, pr.bast_tanggal,
                       COALESCE(pr.bast_penerima, ''),
                       COUNT(d.id) FILTER (WHERE d.versi = 'DRAFT'), COUNT(d.id) FILTER (WHERE d.versi = 'FINAL'),
                       MAX(d.diunggah_pada),
                       (SELECT COUNT(*) FROM lampiran_dokumen ld WHERE ld.entitas = 'proyek' AND ld.entitas_id = pr.id
                        AND ld.jenis = 'BAST_TTD')
                FROM proyek pr
                JOIN klien kl ON kl.id = pr.klien_id
                LEFT JOIN deliverable_proyek d ON d.proyek_id = pr.id
                WHERE pr.status <> 'DIARSIPKAN'
                GROUP BY pr.id, kl.nama_perusahaan
                ORDER BY pr.bast_tanggal IS NOT NULL, MAX(d.diunggah_pada) DESC NULLS LAST, pr.kode_proyek
                """
            )
            rows = [{"id": r[0], "code": r[1], "name": r[2], "client": r[3], "status": r[4], "bast_date": r[5],
                     "bast_receiver": r[6], "drafts": r[7], "finals": r[8], "last": r[9], "bast_signed": r[10]}
                    for r in cursor.fetchall()]
    except DatabaseError:
        messages.error(request, "Data deliverable belum dapat dibaca. Jalankan migrasi database.")
    return render(request, "portal/survey_deliverables.html", {"rows": rows})


@business_permission_required("PROYEK", "LIHAT")
def survey_map(request):
    points = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pr.id, pr.kode_proyek, kl.nama_perusahaan, pr.status, COALESCE(pr.lokasi_teks, ''),
                       pr.latitude, pr.longitude, pr.tanggal_mulai_rencana, pr.tanggal_selesai_rencana,
                       pr.nama, co.kode, COALESCE(pr.progres, 0)
                FROM proyek pr JOIN klien kl ON kl.id = pr.klien_id JOIN perusahaan co ON co.id = pr.perusahaan_id
                WHERE pr.status <> 'DIARSIPKAN'
                ORDER BY pr.tanggal_mulai_rencana DESC NULLS LAST
                """
            )
            for r in cursor.fetchall():
                period = f"{r[7]:%d %b %Y} – {r[8]:%d %b %Y}" if r[7] and r[8] else "Jadwal belum diisi"
                points.append({"id": r[0], "title": r[1], "client": r[2],
                               "status": "SELESAI" if r[3] == "SELESAI" else ("DIRENCANAKAN" if r[3] == "DIRENCANAKAN" else "BERJALAN"),
                               "location": r[4], "lat": float(r[5]) if r[5] is not None else None,
                               "lng": float(r[6]) if r[6] is not None else None, "period": period,
                               "detail": f"{r[9]} · progres {r[11]:g}%", "company": r[10],
                               "url": f"/survey/proyek/{r[0]}/"})
    except DatabaseError:
        messages.error(request, "Data peta proyek belum dapat dibaca.")
    return render(request, "portal/module_map.html", {
        "title": "Peta Proyek Survey", "crumb": "Jasa Survey · Peta Proyek",
        "intro": "Lokasi semua pekerjaan survei — direncanakan, berjalan, dan selesai.",
        "points": points,
        "legend": [("BERJALAN", "Berjalan", "#c8102e"), ("DIRENCANAKAN", "Direncanakan", "#a9682b"),
                   ("SELESAI", "Selesai", "#1f9d63")],
        "missing_hint": "Tandai lokasi proyek dari menu Proyek Survey → Edit.",
    })
