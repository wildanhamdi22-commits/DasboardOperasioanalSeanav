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
    "inventory": {"inventory", "inventory_add", "inventory_edit", "inventory_history", "inventory_archive"},
    "quotation": {"quotation_list", "quotation_create", "quotation_status", "quotation_document"},
    "invoice": {"invoice_list", "invoice_create", "invoice_status", "invoice_document"},
    "rental": {"rental_list", "rental_create", "rental_location_edit"},
    "handover": {"rental_handover"},
    "project": {"project_list", "project_create", "project_edit", "project_archive"},
    "attendance": {"attendance"},
    "leave": {"leave_list", "leave_create", "leave_decide"},
    "attendance_report": {"attendance_report", "attendance_export"},
    "roles": {"role_management"},
    "profile": {"profile"},
}


def portal_shell(request):
    """Data untuk sidebar/topbar: menu aktif, badge, role, dan jumlah peringatan."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    match = getattr(request, "resolver_match", None)
    url_name = match.url_name if match else ""
    nav_active = next((key for key, names in _NAV_GROUPS.items() if url_name in names), "")

    badges = {"units": 0, "quotations": 0, "invoices_overdue": 0, "invoices_open": 0,
              "rentals": 0, "projects": 0, "leave_pending": 0, "alerts": 0}
    role_name = "Super Admin" if user.is_superuser else None
    can_approve_attendance = user.is_superuser
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                  (SELECT COUNT(*) FROM unit_alat WHERE status = 'TERSEDIA'),
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
        "nav_badges": badges,
        "shell_user_name": name,
        "shell_user_initial": (name[:1] or "?").upper(),
        "shell_user_role": role_name or "Staf operasional",
        "can_approve_attendance": can_approve_attendance,
        "shell_db_ok": db_ok,
        "pdf_available": getattr(settings, "PDF_AVAILABLE", True),
    }
