from django.conf import settings
from django.db import DatabaseError, connection


def business_access(request):
    user = getattr(request, "user", None)
    defaults = {
        "dashboard": False,
        "inventory": False,
        "quotation": False,
        "invoice": False,
        "rental": False,
        "project": False,
    }
    if not user or not user.is_authenticated or not user.is_staff:
        return {"business_access": defaults}
    if user.is_superuser:
        return {"business_access": {key: True for key in defaults}}
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT DISTINCT ip.sumber, ip.aksi
                FROM pengguna u
                JOIN pengguna_peran up ON up.pengguna_id = u.id
                JOIN izin_peran ip ON ip.peran_id = up.peran_id
                WHERE lower(u.email) = lower(%s) AND u.aktif
                """,
                [user.email],
            )
            grants = set(cursor.fetchall())
    except DatabaseError:
        grants = set()

    def can_read(source):
        return ((source, "LIHAT") in grants or ("SEMUA_DATA", "LIHAT") in grants
                or ("SEMUA_TRANSAKSI", "LIHAT") in grants
                or ("SEMUA_LAPORAN", "LIHAT") in grants)

    return {"business_access": {
        "dashboard": can_read("SEMUA_DATA"),
        "inventory": can_read("INVENTARIS"),
        "quotation": can_read("QUOTATION"),
        "invoice": can_read("INVOICE"),
        "rental": can_read("RENTAL"),
        "project": can_read("PROYEK"),
    }}


_NAV_GROUPS = {
    "dashboard": {"dashboard"},
    # Rental
    "r_units": {"inventory", "inventory_add", "inventory_edit", "inventory_history", "inventory_archive", "inventory_delete"},
    "r_quotes": {"rental_quotations", "rental_quotation_create"},
    "r_invoices": {"rental_invoices"},
    "r_rentals": {"rental_list", "rental_create", "rental_location_edit", "rental_delivery_order", "rental_archive"},
    "r_returns": {"rental_returns", "rental_handover", "rental_return_note"},
    "r_map": {"rental_map"},
    # Penjualan
    "s_catalog": {"sales_catalog", "sales_catalog_create", "sales_catalog_edit"},
    "s_quotes": {"sales_quotations", "sales_quotation_create"},
    "s_invoices": {"sales_invoices"},
    "s_orders": {"sales_orders", "sales_order_detail"},
    "s_qc": {"sales_qc_list", "sales_qc_form", "sales_qc_report"},
    "s_do": {"sales_deliveries", "sale_delivery_order"},
    "s_map": {"sales_map"},
    # Jasa survey
    "v_quotes": {"survey_quotations", "survey_quotation_create"},
    "v_projects": {"project_list", "project_create", "project_edit", "project_archive", "survey_project_detail"},
    "v_reports": {"survey_reports", "survey_report_document"},
    "v_deliverables": {"survey_deliverables", "survey_bast_document"},
    "v_invoices": {"survey_invoices"},
    "v_map": {"survey_map"},
    # Lainnya
    "attendance": {"attendance"},
    "leave": {"leave_list", "leave_create", "leave_decide"},
    "attendance_report": {"attendance_report", "attendance_export"},
    "attendance_locations": {"attendance_locations"},
    "roles": {"role_management"},
    "profile": {"profile"},
}
_MODULE_OF = {"r": "rental", "s": "sales", "v": "survey"}


def portal_shell(request):
    """Data untuk sidebar/topbar: menu aktif, badge, role, dan jumlah peringatan."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    url_name = match.url_name if match else ""
    nav_active = next((key for key, names in _NAV_GROUPS.items() if url_name in names), "")
    nav_module = _MODULE_OF.get(nav_active.split("_", 1)[0], "") if "_" in nav_active else ""

    badges = {"units": 0, "quotations": 0, "invoices_overdue": 0, "invoices_open": 0,
              "rentals": 0, "projects": 0, "leave_pending": 0, "alerts": 0,
              "returns_due": 0, "orders_open": 0, "qc_pending": 0, "do_missing": 0}
    role_name = "Super Admin" if user.is_superuser else None
    can_approve_attendance = user.is_superuser
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM unit_alat WHERE status = 'TERSEDIA' AND modul = 'RENTAL'),
                  (SELECT COUNT(*) FROM quotation WHERE status IN ('DRAFT','DIAJUKAN','DISETUJUI','TERKIRIM')),
                  (SELECT COUNT(*) FROM invoice WHERE status = 'DITERBITKAN' AND jatuh_tempo < CURRENT_DATE),
                  (SELECT COUNT(*) FROM invoice WHERE status = 'DITERBITKAN'),
                  (SELECT COUNT(*) FROM rental WHERE status = 'BERJALAN'),
                  (SELECT COUNT(*) FROM proyek WHERE status = 'BERJALAN'),
                  (SELECT COUNT(*) FROM rental WHERE status = 'BERJALAN'
                      AND tanggal_selesai_rencana < CURRENT_DATE),
                  (SELECT COUNT(*) FROM quotation WHERE status = 'DIAJUKAN')
                """
            )
            row = cursor.fetchone()
            badges.update(units=row[0], quotations=row[1], invoices_overdue=row[2],
                          invoices_open=row[3], rentals=row[4], projects=row[5])
            badges["alerts"] = row[2] + row[6] + row[7]
            badges["returns_due"] = row[6]
            try:
                with connection.cursor() as extra:
                    extra.execute(
                        """
                        SELECT
                          (SELECT COUNT(*) FROM pesanan_penjualan WHERE status NOT IN ('DIKIRIM', 'DIBATALKAN')),
                          (SELECT COUNT(*) FROM unit_penjualan WHERE status_qc IN ('BELUM', 'GAGAL')),
                          (SELECT COUNT(*) FROM pesanan_penjualan pp WHERE pp.status = 'DIKIRIM' AND NOT EXISTS (
                               SELECT 1 FROM lampiran_dokumen ld WHERE ld.entitas = 'pesanan_penjualan'
                               AND ld.entitas_id = pp.id AND ld.jenis = 'DO_TTD'))
                        """
                    )
                    o = extra.fetchone()
                    badges.update(orders_open=o[0], qc_pending=o[1], do_missing=o[2])
            except DatabaseError:
                pass
            if not user.is_superuser:
                cursor.execute(
                    """
                    SELECT string_agg(DISTINCT p.nama, ', '),
                           bool_or(ip.sumber = 'ABSENSI' AND ip.aksi = 'SETUJUI')
                    FROM pengguna u
                    JOIN pengguna_peran up ON up.pengguna_id = u.id
                    JOIN peran p ON p.id = up.peran_id
                    LEFT JOIN izin_peran ip ON ip.peran_id = p.id
                    WHERE lower(u.email) = lower(%s) AND u.aktif
                    """,
                    [user.email],
                )
                role_row = cursor.fetchone()
                if role_row:
                    role_name = role_row[0]
                    can_approve_attendance = bool(role_row[1])
    except DatabaseError:
        db_ok = False
    try:
        from .models import PengajuanIzin
        if can_approve_attendance:
            badges["leave_pending"] = PengajuanIzin.objects.filter(status=PengajuanIzin.MENUNGGU).count()
        else:
            badges["leave_pending"] = PengajuanIzin.objects.filter(
                user=user, status=PengajuanIzin.MENUNGGU).count()
    except Exception:  # tabel absensi belum dimigrasikan
        pass

    name = user.get_full_name() or user.username
    return {
        "nav_active": nav_active,
        "nav_module": nav_module,
        "nav_badges": badges,
        "shell_user_name": name,
        "shell_user_initial": (name[:1] or "?").upper(),
        "shell_user_role": role_name or "Staf operasional",
        "can_approve_attendance": can_approve_attendance,
        "shell_db_ok": db_ok,
        "pdf_available": getattr(settings, "PDF_AVAILABLE", True),
    }
