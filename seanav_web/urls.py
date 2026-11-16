from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import path, re_path

from portal import attendance as sdm
from portal import document_views as docs
from portal import flag
from portal import rental_extra as rnt
from portal import sales
from portal import survey

from portal.views import (
    add_inventory, archive_project, archive_unit, dashboard, edit_inventory,
    inventory, inventory_history, project_form, project_list, rental_create,
    invoice_create, invoice_edit, invoice_list, invoice_status, place_search,
    quotation_create, quotation_edit, quotation_list, quotation_status,
    rental_handover, rental_list, rental_location_edit, landing, role_management,
    StaffLoginView,
)

RENTAL, SALE, SURVEY = {"kind": "RENTAL"}, {"kind": "PENJUALAN"}, {"kind": "JASA_SURVEY"}

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", landing, name="landing"),
    path("login/", StaffLoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(next_page="landing"), name="logout"),
    path("dashboard/", dashboard, name="dashboard"),
    path("akses-pengguna/", role_management, name="role_management"),

    # ---------------- Rental
    path("inventaris/", inventory, name="inventory"),
    path("inventaris/tambah/", add_inventory, name="inventory_add"),
    path("inventaris/<int:unit_id>/riwayat/", inventory_history, name="inventory_history"),
    path("inventaris/<int:unit_id>/edit/", edit_inventory, name="inventory_edit"),
    path("inventaris/<int:unit_id>/hapus/", rnt.inventory_delete, name="inventory_delete"),
    path("inventaris/<int:unit_id>/<str:action>/", archive_unit, name="inventory_archive"),
    path("rental/quotation/", quotation_list, RENTAL, name="rental_quotations"),
    path("rental/quotation/tambah/", quotation_create, RENTAL, name="rental_quotation_create"),
    path("rental/invoice/", invoice_list, RENTAL, name="rental_invoices"),
    path("rental/serah-terima/", rental_handover, name="rental_handover"),
    path("rental/pengembalian/", rnt.rental_returns, name="rental_returns"),
    path("rental/pengembalian/<int:handover_id>/berita-acara/", docs.rental_return_note, name="rental_return_note"),
    path("rental/peta/", rnt.rental_map, name="rental_map"),
    path("rental/lokasi/cari/", place_search, name="place_search"),
    path("rental/<int:rental_id>/lokasi/", rental_location_edit, name="rental_location_edit"),
    path("rental/<int:rental_id>/arsip/", rnt.rental_archive, name="rental_archive"),
    path("rental/<int:rental_id>/delivery-order/", docs.rental_delivery_order, name="rental_delivery_order"),
    path("delivery-order/<str:kind>/<int:ref_id>/edit/", docs.delivery_order_edit, name="delivery_order_edit"),
    path("rental/tambah/", rental_create, name="rental_create"),
    path("rental/", rental_list, name="rental_list"),

    # ---------------- Penjualan
    path("penjualan/katalog/", sales.sales_catalog, name="sales_catalog"),
    path("penjualan/katalog/tambah/", sales.sales_catalog_form, name="sales_catalog_create"),
    path("penjualan/katalog/<int:product_id>/edit/", sales.sales_catalog_form, name="sales_catalog_edit"),
    path("penjualan/katalog/<int:product_id>/hapus/", sales.sales_catalog_delete, name="sales_catalog_delete"),
    path("penjualan/quotation/", quotation_list, SALE, name="sales_quotations"),
    path("penjualan/quotation/tambah/", quotation_create, SALE, name="sales_quotation_create"),
    path("penjualan/invoice/", invoice_list, SALE, name="sales_invoices"),
    path("penjualan/pesanan/", sales.sales_orders, name="sales_orders"),
    path("penjualan/pesanan/dari-quotation/<int:quotation_id>/", sales.sales_order_create, name="sales_order_create"),
    path("penjualan/pesanan/<int:order_id>/", sales.sales_order_detail, name="sales_order_detail"),
    path("penjualan/pesanan/<int:order_id>/delivery-order/", docs.sale_delivery_order, name="sale_delivery_order"),
    path("penjualan/qc/", sales.sales_qc_list, name="sales_qc_list"),
    path("penjualan/qc/<int:unit_id>/", sales.sales_qc_form, name="sales_qc_form"),
    path("penjualan/qc/<int:unit_id>/report/", docs.sales_qc_report, name="sales_qc_report"),
    path("penjualan/do/", sales.sales_deliveries, name="sales_deliveries"),
    path("penjualan/peta/", sales.sales_map, name="sales_map"),

    # ---------------- Jasa Survey
    path("survey/quotation/", quotation_list, SURVEY, name="survey_quotations"),
    path("survey/quotation/tambah/", quotation_create, SURVEY, name="survey_quotation_create"),
    path("survey/invoice/", invoice_list, SURVEY, name="survey_invoices"),
    path("survey/proyek/<int:project_id>/", survey.survey_project_detail, name="survey_project_detail"),
    path("survey/proyek/<int:project_id>/bast/", docs.survey_bast_document, name="survey_bast_document"),
    path("survey/laporan-harian/", survey.survey_reports, name="survey_reports"),
    path("survey/laporan-harian/<int:report_id>/dokumen/", docs.survey_report_document, name="survey_report_document"),
    path("survey/deliverable/", survey.survey_deliverables, name="survey_deliverables"),
    path("survey/peta/", survey.survey_map, name="survey_map"),
    # ---------------- Pinjam bendera (LPJ klien) — terpisah dari quotation/rental
    path("pinjam-bendera/", flag.flag_list, name="flag_list"),
    path("pinjam-bendera/tambah/", flag.flag_form, name="flag_create"),
    path("pinjam-bendera/impor/", flag.flag_import, name="flag_import"),
    path("pinjam-bendera/ekspor/", flag.flag_export, name="flag_export"),
    path("pinjam-bendera/<int:flag_id>/edit/", flag.flag_form, name="flag_edit"),
    path("pinjam-bendera/<int:flag_id>/dokumen/<str:doc>/", flag.flag_document, name="flag_document"),
    path("pinjam-bendera/<int:flag_id>/<str:action>/", flag.flag_status, name="flag_status"),
    path("proyek/", project_list, name="project_list"),
    path("proyek/tambah/", project_form, name="project_create"),
    path("proyek/<int:project_id>/edit/", project_form, name="project_edit"),
    path("proyek/<int:project_id>/<str:action>/", archive_project, name="project_archive"),

    # ---------------- Dokumen & status bersama (quotation / invoice semua modul)
    path("quotation/", quotation_list, name="quotation_list"),
    path("quotation/tambah/", quotation_create, name="quotation_create"),
    path("quotation/<int:quotation_id>/dokumen/", docs.quotation_document, name="quotation_document"),
    path("quotation/<int:quotation_id>/edit/", quotation_edit, name="quotation_edit"),
    path("quotation/<int:quotation_id>/<str:action>/", quotation_status, name="quotation_status"),
    path("invoice/", invoice_list, name="invoice_list"),
    path("invoice/tambah/<int:quotation_id>/", invoice_create, name="invoice_create"),
    path("invoice/<int:invoice_id>/dokumen/", docs.invoice_document, name="invoice_document"),
    path("invoice/<int:invoice_id>/edit/", invoice_edit, name="invoice_edit"),
    path("invoice/<int:invoice_id>/<str:action>/", invoice_status, name="invoice_status"),

    # ---------------- SDM: absensi, izin/cuti, rekap, profil
    path("absensi/", sdm.attendance, name="attendance"),
    path("absensi/rekap/", sdm.attendance_report, name="attendance_report"),
    path("absensi/lokasi-kantor/", sdm.attendance_locations, name="attendance_locations"),
    path("absensi/rekap/ekspor/", sdm.attendance_export, name="attendance_export"),
    path("izin/", sdm.leave_list, name="leave_list"),
    path("izin/ajukan/", sdm.leave_create, name="leave_create"),
    path("izin/<int:pk>/<str:action>/", sdm.leave_decide, name="leave_decide"),
    path("profil/", sdm.profile, name="profile"),
    # File unggahan hanya untuk staf yang login (foto absensi, lampiran, arsip dokumen)
    re_path(r"^media/(?P<path>.+)$", sdm.protected_media, name="protected_media"),
]

handler403 = "portal.views.permission_denied_view"
handler404 = "portal.views.page_not_found"
handler500 = "portal.views.server_error"
