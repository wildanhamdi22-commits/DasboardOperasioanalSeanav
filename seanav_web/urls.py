from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path, re_path

from portal import attendance as sdm

from portal.views import (
    add_inventory, archive_project, archive_unit, dashboard, edit_inventory,
    inventory, inventory_history, project_form, project_list, rental_create,
    invoice_create, invoice_document, invoice_list, invoice_status, place_search,
    quotation_create, quotation_document, quotation_list, quotation_status, sale_delivery,
    rental_handover, rental_list, rental_location_edit, landing, role_management,
    StaffLoginView,
)


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", landing, name="landing"),
    path("login/", StaffLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="landing"), name="logout"),
    path("dashboard/", dashboard, name="dashboard"),
    path("akses-pengguna/", role_management, name="role_management"),
    path("inventaris/", inventory, name="inventory"),
    path("inventaris/<int:unit_id>/riwayat/", inventory_history, name="inventory_history"),
    path("rental/serah-terima/", rental_handover, name="rental_handover"),
    path("rental/lokasi/cari/", place_search, name="place_search"),
    path("rental/<int:rental_id>/lokasi/", rental_location_edit, name="rental_location_edit"),
    path("rental/tambah/", rental_create, name="rental_create"),
    path("rental/", rental_list, name="rental_list"),
    path("quotation/", quotation_list, name="quotation_list"),
    path("quotation/tambah/", quotation_create, name="quotation_create"),
    path("quotation/<int:quotation_id>/dokumen/", quotation_document, name="quotation_document"),
    path("quotation/<int:quotation_id>/kirim-barang/", sale_delivery, name="sale_delivery"),
    path("quotation/<int:quotation_id>/<str:action>/", quotation_status, name="quotation_status"),
    path("invoice/", invoice_list, name="invoice_list"),
    path("invoice/tambah/<int:quotation_id>/", invoice_create, name="invoice_create"),
    path("invoice/<int:invoice_id>/dokumen/", invoice_document, name="invoice_document"),
    path("invoice/<int:invoice_id>/<str:action>/", invoice_status, name="invoice_status"),
    path("inventaris/tambah/", add_inventory, name="inventory_add"),
    path("inventaris/<int:unit_id>/edit/", edit_inventory, name="inventory_edit"),
    path("inventaris/<int:unit_id>/<str:action>/", archive_unit, name="inventory_archive"),
    path("proyek/", project_list, name="project_list"),
    path("proyek/tambah/", project_form, name="project_create"),
    path("proyek/<int:project_id>/edit/", project_form, name="project_edit"),
    path("proyek/<int:project_id>/<str:action>/", archive_project, name="project_archive"),
    # SDM: absensi, izin/cuti, rekap, profil
    path("absensi/", sdm.attendance, name="attendance"),
    path("absensi/rekap/", sdm.attendance_report, name="attendance_report"),
    path("absensi/rekap/ekspor/", sdm.attendance_export, name="attendance_export"),
    path("izin/", sdm.leave_list, name="leave_list"),
    path("izin/ajukan/", sdm.leave_create, name="leave_create"),
    path("izin/<int:pk>/<str:action>/", sdm.leave_decide, name="leave_decide"),
    path("profil/", sdm.profile, name="profile"),
    # File unggahan hanya untuk staf yang login (foto absensi, lampiran izin, foto alat)
    re_path(r"^media/(?P<path>.+)$", sdm.protected_media, name="protected_media"),
]

handler403 = "portal.views.permission_denied_view"
handler404 = "portal.views.page_not_found"
handler500 = "portal.views.server_error"


