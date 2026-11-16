"""Unduhan dokumen resmi (Quotation, Invoice, Delivery Order) memakai satu desain
dari portal.doc_builder: Oseana = Century Gothic, Seanav = Aptos."""
import logging
from datetime import date, datetime
from decimal import Decimal

from django.contrib import messages
from django.db import DatabaseError, connection
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.utils import timezone

from .doc_builder import (
    BRANDS, build_delivery_order, build_generic, build_invoice, build_quotation, default_terms,
)
from .modules import QC_CHECKLIST, RETURN_CHECKLIST, as_json
from .pdf import PdfConversionError, docx_to_pdf
from .views import business_permission_required

logger = logging.getLogger(__name__)

MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _d(value):
    if not value:
        return "-"
    if isinstance(value, datetime):
        value = timezone.localtime(value) if timezone.is_aware(value) else value
        return f"{value.day} {MONTHS[value.month - 1]} {value.year}, {value:%H:%M}"
    return f"{value.day} {MONTHS[value.month - 1]} {value.year}"


def _lines(text):
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _brand_code(code):
    return code if code in BRANDS else "SNV"


def _respond(request, docx_bytes, number, fallback):
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in number)
    if request.GET.get("format", "").lower() == "pdf":
        try:
            pdf = docx_to_pdf(docx_bytes, safe)
        except PdfConversionError as exc:
            logger.error("PDF %s gagal: %s", number, exc)
            messages.error(request, "PDF belum berhasil dibuat. Unduh versi DOCX, atau pastikan Microsoft Word "
                                    "(Windows) / LibreOffice (server) terpasang.")
            return redirect(fallback)
        response = HttpResponse(pdf, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{safe}.pdf"'
        return response
    response = HttpResponse(docx_bytes, content_type=DOCX_MIME)
    response["Content-Disposition"] = f'attachment; filename="{safe}.docx"'
    return response


def _client_block(name, address, phone, email, attn):
    lines = [name, *_lines(address)]
    contact = "  ·  ".join(x for x in (phone, email) if x)
    if contact:
        lines.append(contact)
    lines.append(f"Attn. {attn or '-'}")
    return {"name": name, "address": lines[1:-1], "attn": attn or "-"}


def _priced_items(cursor, quotation_id):
    """Item quotation dikelompokkan per kategori alat (I., II., ...)."""
    cursor.execute(
        """
        SELECT qi.deskripsi_item, qi.kuantitas, qi.satuan, qi.harga_satuan,
               qi.tanggal_mulai_rencana, qi.tanggal_selesai_rencana, COALESCE(k.nama, ''),
               COALESCE((
                   SELECT string_agg(a.item_text, ', ' ORDER BY a.item_text)
                   FROM (
                       SELECT DISTINCT trim_scale(pui.jumlah)::text || '× ' || pai.nama_item AS item_text
                       FROM unit_alat u
                       JOIN paket_unit pu ON pu.unit_utama_id = u.id
                       JOIN paket_unit_item pui ON pui.paket_unit_id = pu.id
                       JOIN paket_alat_item pai ON pai.id = pui.paket_alat_item_id
                       WHERE u.model_alat_id = qi.model_alat_id
                   ) a
               ), '')
        FROM quotation_item qi
        LEFT JOIN model_alat m ON m.id = qi.model_alat_id
        LEFT JOIN kategori_alat k ON k.id = m.kategori_alat_id
        WHERE qi.quotation_id = %s
        ORDER BY qi.id
        """,
        [quotation_id],
    )
    rows = cursor.fetchall()
    groups = []
    by_group = {}
    for desc, qty, unit, price, start, end, category, accessories in rows:
        key = category or "Other items"
        if key not in by_group:
            by_group[key] = []
            groups.append(key)
        days = (end - start).days + 1 if start and end else None
        details = []
        if start and end:
            details.append(f"{_d(start)} – {_d(end)}")
        if accessories:
            details.append(f"Incl. {accessories}")
        by_group[key].append({"name": desc, "details": details, "qty": qty, "unit": unit or "unit",
                              "days": days, "price": price})
    items = []
    use_groups = len(groups) > 1 or (groups and groups[0] != "Other items")
    for index, key in enumerate(groups):
        if use_groups:
            items.append({"group": f"{ROMAN[index] if index < len(ROMAN) else index + 1}. {key}"})
        items.extend(by_group[key])
    period = [r[4] for r in rows if r[4]], [r[5] for r in rows if r[5]]
    return items, (min(period[0]) if period[0] else None, max(period[1]) if period[1] else None)


def _tax_label(tax_name, tax_rate):
    rate = Decimal(tax_rate or 0)
    return f"{tax_name or 'PPN'} {rate.normalize():f}%"


@business_permission_required("QUOTATION", "LIHAT")
def quotation_document(request, quotation_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT q.nomor, q.tanggal, q.berlaku_sampai, q.catatan, p.kode,
                       kl.nama_perusahaan, COALESCE(kl.alamat, ''), COALESCE(kl.email, ''),
                       COALESCE(kl.telepon, ''),
                       COALESCE((SELECT kk.nama FROM kontak_klien kk WHERE kk.klien_id = kl.id
                                 ORDER BY kk.utama DESC, kk.id LIMIT 1), ''),
                       COALESCE(bd.nama, ''), COALESCE(bd.email, ''),
                       q.subtotal, q.tarif_pajak, q.total_pajak, q.jenis_pajak, q.total, q.jenis
                FROM quotation q
                JOIN perusahaan p ON p.id = q.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                LEFT JOIN pengguna bd ON bd.id = q.pic_pengguna_id
                WHERE q.id = %s
                """,
                [quotation_id],
            )
            q = cursor.fetchone()
            if not q:
                raise Http404("Quotation tidak ditemukan.")
            items, (start, end) = _priced_items(cursor, quotation_id)
    except DatabaseError:
        messages.error(request, "Data quotation belum dapat dibaca dari database.")
        return redirect("quotation_list")

    company = _brand_code(q[4])
    kind = q[17] or "RENTAL"
    client = _client_block(q[5], q[6], q[8], q[7], q[9])
    site_lines = client["address"][:2] or ["As per Purchase Order"]
    if start and end:
        site_lines = site_lines + [f"Period: {_d(start)} – {_d(end)}"]
    terms = list(default_terms(company, kind))
    if q[3]:
        terms.append(("Notes", _lines(q[3])))
    data = {
        "kind": kind, "number": q[0], "date": _d(q[1]), "valid_until": _d(q[2]),
        "lead_time": "1 day after PO" if kind == "RENTAL" else "As agreed",
        "client": client,
        "site": {"name": "Ship to / site", "lines": site_lines},
        "rep": {"name": q[10] or "Business Development", "email": q[11] or "-"},
        "items": items, "terms": terms,
        "subtotal": q[12], "tax": q[14] or 0, "total": q[16],
        "tax_rate": "0.11" if q[14] else None,
        "tax_label": _tax_label(q[15], q[13]) if q[14] else None,
    }
    data["site"]["name"] = q[5]
    return _respond(request, build_quotation(company, data), q[0], "quotation_list")


INVOICE_STATUS = {"DIBAYAR": "PAID", "DIBATALKAN": "CANCELLED"}


@business_permission_required("INVOICE", "LIHAT")
def invoice_document(request, invoice_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT i.nomor, i.tanggal, i.jatuh_tempo, i.po_no, i.status,
                       i.subtotal, i.jenis_pajak, i.tarif_pajak, i.total_pajak, i.total,
                       i.syarat_pembayaran, i.catatan, q.id, q.nomor, co.kode,
                       kl.nama_perusahaan, COALESCE(kl.alamat, ''), COALESCE(kl.email, ''),
                       COALESCE(kl.telepon, ''),
                       COALESCE((SELECT kk.nama FROM kontak_klien kk WHERE kk.klien_id = kl.id
                                 ORDER BY kk.utama DESC, kk.id LIMIT 1), ''),
                       q.jenis, i.termin_ke, i.persen_termin, COALESCE(i.keterangan_termin, ''), q.subtotal
                FROM invoice i
                JOIN quotation q ON q.id = i.quotation_id
                JOIN perusahaan co ON co.id = i.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                WHERE i.id = %s
                """,
                [invoice_id],
            )
            inv = cursor.fetchone()
            if not inv:
                raise Http404("Invoice tidak ditemukan.")
            items, (start, end) = _priced_items(cursor, inv[12])
    except DatabaseError:
        messages.error(request, "Invoice belum dapat dibaca dari database.")
        return redirect("invoice_list")

    company = _brand_code(inv[14])
    kind = inv[20] or "RENTAL"
    kind_text = {"RENTAL": "Equipment rental", "PENJUALAN": "Product sale", "JASA_SURVEY": "Survey services"}[kind] \
        if kind in ("RENTAL", "PENJUALAN", "JASA_SURVEY") else kind
    project_lines = []
    if start and end:
        days = (end - start).days + 1
        project_lines.append(f"Period: {_d(start)} – {_d(end)} ({days} day{'s' if days != 1 else ''})")
    project_lines += _lines(inv[11])[:2]
    payment_terms = _lines(inv[10]) or ["As stated in the quotation / Purchase Order."]
    if inv[21]:
        items = [{"name": f"Termin {inv[21]} — {inv[23] or 'Progress payment'}",
                  "details": [f"{Decimal(inv[22]).normalize():f}% of contract value IDR {Decimal(inv[24]):,.0f} (excl. tax)".replace(",", ".")
                              if inv[24] else f"{Decimal(inv[22]).normalize():f}% of contract value",
                              f"Survey services as per quotation {inv[13]}"],
                  "qty": 1, "unit": "lot", "days": None, "price": inv[5]}]
    data = {
        "kind": kind, "number": inv[0], "date": _d(inv[1]), "due_date": _d(inv[2]),
        "quotation": inv[13], "po": inv[3] or "-",
        "status": INVOICE_STATUS.get(inv[4]),
        "client": _client_block(inv[15], inv[16], inv[18], inv[17], inv[19]),
        "project": f"{kind_text} — quotation {inv[13]}", "project_lines": project_lines,
        "items": items, "payment_terms": payment_terms,
        "subtotal": inv[5], "tax": inv[8] or 0, "total": inv[9],
        "tax_rate": "0.11" if inv[8] else None,
        "tax_label": _tax_label(inv[6], inv[7]) if inv[8] else None,
        "paid": inv[9] if inv[4] == "DIBAYAR" else None,
        "signer": "Finance Department",
    }
    return _respond(request, build_invoice(company, data), inv[0], "invoice_list")


def _accessories_for_unit(cursor, unit_id):
    if not unit_id:
        return ""
    cursor.execute(
        """
        SELECT string_agg(trim_scale(pui.jumlah)::text || '× ' || pai.nama_item
                          || COALESCE(' (S/N ' || NULLIF(pui.nomor_seri, '') || ')', ''),
                          ', ' ORDER BY pai.nama_item)
        FROM paket_unit pu
        JOIN paket_unit_item pui ON pui.paket_unit_id = pu.id
        JOIN paket_alat_item pai ON pai.id = pui.paket_alat_item_id
        WHERE pu.unit_utama_id = %s
        """,
        [unit_id],
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else ""


CONDITION_EN = {"BAIK": "Good", "RUSAK_RINGAN": "Minor damage", "RUSAK_BERAT": "Damaged", "PERLU_KALIBRASI": "Needs calibration"}


@business_permission_required("SERAH_TERIMA", "LIHAT")
def rental_delivery_order(request, rental_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.nomor_rental, r.tanggal_mulai_rencana, r.tanggal_selesai_rencana,
                       COALESCE(r.lokasi_penggunaan, ''), co.kode, kl.nama_perusahaan,
                       COALESCE(kl.alamat, ''),
                       COALESCE((SELECT kk.nama FROM kontak_klien kk WHERE kk.klien_id = kl.id
                                 ORDER BY kk.utama DESC, kk.id LIMIT 1), ''),
                       COALESCE(q.nomor, '-'), COALESCE(i.po_no, '-'),
                       COALESCE(bd.nama, ''), COALESCE(bd.email, ''),
                       st.tanggal, COALESCE(st.penerima_nama, ''), COALESCE(pic.nama, ''),
                       COALESCE(st.catatan, '')
                FROM rental r
                JOIN perusahaan co ON co.id = r.perusahaan_id
                JOIN klien kl ON kl.id = r.klien_id
                LEFT JOIN quotation q ON q.id = r.quotation_id
                LEFT JOIN invoice i ON i.id = r.invoice_id
                LEFT JOIN pengguna bd ON bd.id = q.pic_pengguna_id
                LEFT JOIN LATERAL (
                    SELECT * FROM serah_terima_rental s WHERE s.rental_id = r.id AND s.jenis = 'KELUAR'
                    ORDER BY s.tanggal LIMIT 1
                ) st ON TRUE
                LEFT JOIN pengguna pic ON pic.id = st.pic_internal_id
                WHERE r.id = %s
                """,
                [rental_id],
            )
            r = cursor.fetchone()
            if not r:
                raise Http404("Rental tidak ditemukan.")
            cursor.execute(
                """
                SELECT TRIM(COALESCE(m.merek, '') || ' ' || COALESCE(m.nama_model, qi.deskripsi_item, 'Item')),
                       COALESCE(u.nomor_seri, '-'), ri.kuantitas, COALESCE(qi.satuan, 'unit'),
                       COALESCE(ri.kondisi_keluar, u.kondisi, 'BAIK'), u.id
                FROM rental_item ri
                LEFT JOIN unit_alat u ON u.id = ri.unit_alat_id
                LEFT JOIN model_alat m ON m.id = COALESCE(ri.model_alat_id, u.model_alat_id)
                LEFT JOIN quotation_item qi ON qi.id = ri.quotation_item_id
                WHERE ri.rental_id = %s
                ORDER BY ri.id
                """,
                [rental_id],
            )
            items = []
            for name, serial, qty, unit, condition, unit_id in cursor.fetchall():
                accessories = _accessories_for_unit(cursor, unit_id)
                items.append({"name": name, "details": [f"Incl. {accessories}"] if accessories else [],
                              "serial": serial, "qty": qty, "unit": unit,
                              "condition": CONDITION_EN.get(condition, condition.title())})
    except DatabaseError:
        messages.error(request, "Data rental belum dapat dibaca dari database.")
        return redirect("rental_list")

    company = _brand_code(r[4])
    site_lines = _lines(r[3])[:2] or _lines(r[6])[:2]
    data = {
        "number": f"DO/{r[0]}", "date": _d(r[12].date() if r[12] else r[1]),
        "reference": r[8], "po": r[9], "purpose": "Rental equipment delivery",
        "client": {"name": r[5], "attn": r[13] or r[7] or "-"},
        "site_lines": site_lines + [f"Rental period: {_d(r[1])} – {_d(r[2])}"],
        "ship_via": "Handover" if not r[12] else f"Handover {_d(r[12])}",
        "ship_lines": _lines(r[15])[:2],
        "rep": {"name": r[10] or "Operations", "email": r[11] or "-", "phone": ""},
        "prepared_by": r[14], "delivered_by": "",
        "items": items,
    }
    return _respond(request, build_delivery_order(company, data), data["number"], "rental_list")




def _company_of(cursor, sql, params):
    cursor.execute(sql, params)
    row = cursor.fetchone()
    return row


@business_permission_required("SERAH_TERIMA", "LIHAT")
def rental_return_note(request, handover_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT s.id, s.tanggal, COALESCE(s.penerima_nama, ''), COALESCE(pg.nama, ''), COALESCE(s.catatan, ''),
                       s.checklist, r.nomor_rental, co.kode, kl.nama_perusahaan, COALESCE(q.nomor, '-'),
                       r.tanggal_mulai_rencana, r.tanggal_selesai_rencana
                FROM serah_terima_rental s
                JOIN rental r ON r.id = s.rental_id
                JOIN perusahaan co ON co.id = r.perusahaan_id
                JOIN klien kl ON kl.id = r.klien_id
                LEFT JOIN quotation q ON q.id = r.quotation_id
                LEFT JOIN pengguna pg ON pg.id = s.pic_internal_id
                WHERE s.id = %s AND s.jenis = 'KEMBALI'
                """,
                [handover_id],
            )
            h = cursor.fetchone()
            if not h:
                raise Http404("Catatan pengembalian tidak ditemukan.")
            cursor.execute(
                """
                SELECT TRIM(concat_ws(' ', m.merek, m.nama_model)), COALESCE(u.nomor_seri, '-'), si.jumlah,
                       COALESCE(si.kondisi, '-'), u.id
                FROM serah_terima_rental_item si
                LEFT JOIN unit_alat u ON u.id = si.unit_alat_id
                LEFT JOIN model_alat m ON m.id = u.model_alat_id
                WHERE si.serah_terima_id = %s ORDER BY si.id
                """,
                [handover_id],
            )
            rows = []
            for i, (name, serial, qty, cond, unit_id) in enumerate(cursor.fetchall(), 1):
                acc = _accessories_for_unit(cursor, unit_id)
                rows.append({"cells": [str(i), [name, *([f"Incl. {acc}"] if acc else [])], serial,
                                       f"{Decimal(qty).normalize():f} unit", CONDITION_EN.get(cond, cond.title())]})
    except DatabaseError:
        messages.error(request, "Data pengembalian belum dapat dibaca.")
        return redirect("rental_returns")
    checklist = as_json(h[5]) or {}
    labels = {"lengkap": "Units & accessories complete as per delivery order",
              "fisik": "No new physical damage", "fungsi": "Powers on and functions normally",
              "bersih": "Returned clean and dry"}
    number = f"RN/{h[6]}/{h[0]}"
    data = {
        "title": "RETURN NOTE", "subtitle": "Rental equipment return", "number": number, "footer_title": "Return Note",
        "meta": [("Note No.", number), ("Date", _d(h[1])), ("Rental", h[6]), ("Quotation", h[9])],
        "parties": [("Returned by", [h[8], h[2] or "-"]), ("Rental period", [f"{_d(h[10])} – {_d(h[11])}"]),
                    ("Received by (warehouse)", [h[3] or "-"])],
        "sections": [
            {"table": {"columns": ["No", "Item & accessories", "Serial no.", "Qty", "Condition"],
                       "widths": [10, None, 38, 22, 30], "rows": rows, "center": (0, 3, 4)}},
            {"heading": "Return inspection"},
            {"table": {"columns": ["No", "Check item", "Result"], "widths": [10, None, 30],
                       "rows": [{"cells": [str(i), labels.get(k, k), "✓ OK" if checklist.get(k) else "✗ Not OK"]}
                                for i, (k, _) in enumerate(RETURN_CHECKLIST, 1)] if any(k in checklist for k, _ in RETURN_CHECKLIST) else
                               [{"cells": ["–", "Inspection checklist not recorded", "–"]}],
                       "center": (0, 2)}},
            *([{"heading": "Accessories check"},
               {"table": {"columns": ["No", "Accessory", "Qty", "Serial no.", "Result"], "widths": [10, None, 16, 38, 26],
                          "rows": [{"cells": [str(i), a.get("nama", ""), str(a.get("jumlah", "")), a.get("serial") or "-",
                                              "✓ Returned" if a.get("ada") else "✗ Missing"]}
                                   for i, a in enumerate(checklist.get("aksesori", []), 1)],
                          "center": (0, 2, 4)}}] if checklist.get("aksesori") else []),
            {"box": {"title": "Remarks", "lines": [(h[4] or "No remarks.", {"size": 8.5})]}},
        ],
        "signatures": [("Returned by (client)", h[2], "Name, date & signature"),
                       ("Received by (warehouse)", h[3], "Date :")],
    }
    return _respond(request, build_generic(_brand_code(h[7]), data), number, "rental_returns")


@business_permission_required("SERAH_TERIMA", "LIHAT")
def sale_delivery_order(request, order_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pp.id, pp.nomor_pesanan, co.kode, kl.nama_perusahaan, COALESCE(pp.alamat_kirim, kl.alamat, ''),
                       COALESCE(q.nomor, '-'), COALESCE((SELECT i.po_no FROM invoice i WHERE i.quotation_id = q.id
                                                         ORDER BY i.id LIMIT 1), '-'),
                       COALESCE(bd.nama, ''), COALESCE(bd.email, ''), pp.tanggal_kirim, COALESCE(pp.penerima, ''),
                       COALESCE(pp.pengirim, ''), COALESCE(pp.catatan, ''), COALESCE(cr.nama, '')
                FROM pesanan_penjualan pp
                JOIN perusahaan co ON co.id = pp.perusahaan_id
                JOIN klien kl ON kl.id = pp.klien_id
                LEFT JOIN quotation q ON q.id = pp.quotation_id
                LEFT JOIN pengguna bd ON bd.id = q.pic_pengguna_id
                LEFT JOIN pengguna cr ON cr.id = pp.dibuat_oleh
                WHERE pp.id = %s
                """,
                [order_id],
            )
            o = cursor.fetchone()
            if not o:
                raise Http404("Pesanan tidak ditemukan.")
            cursor.execute(
                """
                SELECT i.id, i.deskripsi_snapshot, i.kuantitas, COALESCE(qi.satuan, 'unit'),
                       COALESCE(p.garansi, '')
                FROM pesanan_penjualan_item i
                LEFT JOIN quotation_item qi ON qi.id = i.quotation_item_id
                LEFT JOIN produk_jual p ON p.id = i.produk_jual_id
                WHERE i.pesanan_id = %s ORDER BY i.id
                """,
                [order_id],
            )
            order_items = cursor.fetchall()
            items = []
            for item_id, name, qty, unit, warranty in order_items:
                cursor.execute(
                    "SELECT nomor_seri, status_qc FROM unit_penjualan WHERE pesanan_item_id = %s ORDER BY id", [item_id])
                units = cursor.fetchall()
                details = [f"Warranty: {warranty}"] if warranty else []
                if units:
                    for serial, qc in units:
                        items.append({"name": name, "details": details + ["QC passed before delivery"
                                                                           if qc in ("LULUS", "CATATAN") else ""],
                                      "serial": serial, "qty": 1, "unit": unit, "condition": "New"})
                else:
                    items.append({"name": name, "details": details, "serial": "-", "qty": qty, "unit": unit,
                                  "condition": "New"})
    except DatabaseError:
        messages.error(request, "Data pengiriman belum dapat dibaca.")
        return redirect("sales_deliveries")
    company = _brand_code(o[2])
    number = "DO/" + (o[1][3:] if o[1].startswith("SO/") else o[1])
    data = {
        "number": number, "date": _d(o[9].date() if o[9] else date.today()),
        "reference": f"{o[5]}", "po": o[6], "purpose": "Product delivery",
        "client": {"name": o[3], "attn": o[10] or "-"},
        "site_lines": _lines(o[4])[:2],
        "ship_via": o[11] or "Company delivery",
        "ship_lines": [f"Shipped {_d(o[9])}"] if o[9] else [],
        "rep": {"name": o[7] or "Sales", "email": o[8] or "-", "phone": ""},
        "prepared_by": o[13], "delivered_by": o[11],
        "items": items,
    }
    return _respond(request, build_delivery_order(company, data), number, "sales_deliveries")


QC_SECTIONS_EN = {"A": "Identity & documents", "B": "Physical condition", "C": "Completeness",
                  "D": "Function test", "E": "Software & licence", "F": "Photo documentation"}
QC_RESULT_EN = {"LULUS": "PASSED", "CATATAN": "PASSED WITH REMARKS", "GAGAL": "FAILED"}
QC_POINTS_EN = {
    "sn_cocok": "Serial number on unit matches packing list / manufacturer invoice",
    "garansi": "Warranty card available", "sertifikat": "Calibration / factory test certificate available",
    "manual": "Manual / user guide available", "kemasan": "Factory packaging & seals intact",
    "cacat": "No defects, cracks or scratches", "konektor": "Connectors & cables in good condition",
    "aksesori": "Accessories complete as per packing list", "jumlah": "Quantity matches order",
    "power": "Unit powers on normally", "selftest": "Self-test / diagnostics passed",
    "uji_fungsi": "Functional test passed (position fix / data / sensor reading)",
    "baterai": "Battery charged & charging normally", "lisensi": "Software licence / activation installed",
    "foto": "Photos of unit, serial plate, accessories & packaging taken",
}


@business_permission_required("SERAH_TERIMA", "LIHAT")
def sales_qc_report(request, unit_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT up.nomor_seri, COALESCE(up.sn_packing_list, ''), i.deskripsi_snapshot, pp.nomor_pesanan,
                       co.kode, kl.nama_perusahaan, qc.id, qc.hasil, qc.diperiksa_pada, COALESCE(qc.firmware, ''),
                       COALESCE(qc.catatan, ''), qc.checklist, COALESCE(pg.nama, ''), up.tanggal_datang,
                       COALESCE(pp.pemasok, '')
                FROM unit_penjualan up
                JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
                JOIN pesanan_penjualan pp ON pp.id = i.pesanan_id
                JOIN perusahaan co ON co.id = pp.perusahaan_id
                JOIN klien kl ON kl.id = pp.klien_id
                JOIN LATERAL (SELECT * FROM qc_penjualan q WHERE q.unit_penjualan_id = up.id
                              ORDER BY q.diperiksa_pada DESC LIMIT 1) qc ON TRUE
                LEFT JOIN pengguna pg ON pg.id = qc.pemeriksa_id
                WHERE up.id = %s
                """,
                [unit_id],
            )
            r = cursor.fetchone()
    except DatabaseError:
        r = None
    if not r:
        messages.error(request, "Unit ini belum memiliki hasil QC.")
        return redirect("sales_qc_list")
    checklist = as_json(r[11]) or {}
    sections = []
    for title, points in QC_CHECKLIST:
        sections.append({"table": {"columns": [QC_SECTIONS_EN.get(title[:1], title), "Result"],
                                   "widths": [None, 30],
                                   "rows": [{"cells": [QC_POINTS_EN.get(k, label), "✓ OK" if checklist.get(k) else "✗"]}
                                            for k, label in points],
                                   "center": (1,)}})
    number = f"QC/{r[3][3:] if r[3].startswith('SO/') else r[3]}/{r[6]}"
    data = {
        "title": "QC REPORT", "subtitle": "Pre-delivery inspection", "number": number, "footer_title": "QC Report",
        "meta": [("Report No.", number), ("Inspection date", _d(r[8])), ("Order", r[3]), ("Result", QC_RESULT_EN.get(r[7], r[7]))],
        "parties": [("Customer", [r[5]]), ("Product", [r[2], f"S/N {r[0]}", f"Firmware {r[9] or '-'}"]),
                    ("Arrival", [_d(r[13]), r[14] or "-", f"Packing list S/N {r[1] or '-'}"])],
        "sections": sections + [{"box": {"title": f"Result: {QC_RESULT_EN.get(r[7], r[7])}",
                                         "lines": [(r[10] or "All inspection points passed.", {"size": 8.5})]}}],
        "signatures": [("Inspected by", r[12], "QC / Technical"), ("Approved by", "", "Date :")],
    }
    return _respond(request, build_generic(_brand_code(r[4]), data), number, "sales_qc_list")


@business_permission_required("PROYEK", "LIHAT")
def survey_report_document(request, report_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT l.tanggal, l.aktivitas, COALESCE(l.cuaca, ''), l.jam_kerja, l.jam_standby, l.progres,
                       COALESCE(l.kendala, ''), COALESCE(pg.nama, ''), pr.kode_proyek, pr.nama, co.kode,
                       kl.nama_perusahaan, COALESCE(pr.lokasi_teks, ''), COALESCE(pr.kapal, ''), COALESCE(pr.kru, ''),
                       pr.id, (SELECT COUNT(*) FROM laporan_harian x WHERE x.proyek_id = pr.id AND x.tanggal <= l.tanggal)
                FROM laporan_harian l
                JOIN proyek pr ON pr.id = l.proyek_id
                JOIN perusahaan co ON co.id = pr.perusahaan_id
                JOIN klien kl ON kl.id = pr.klien_id
                LEFT JOIN pengguna pg ON pg.id = l.dibuat_oleh
                WHERE l.id = %s
                """,
                [report_id],
            )
            r = cursor.fetchone()
            if not r:
                raise Http404("Laporan tidak ditemukan.")
    except DatabaseError:
        messages.error(request, "Laporan harian belum dapat dibaca.")
        return redirect("survey_reports")
    number = f"DPR/{r[8]}/{r[16]:03d}"
    activities = [{"cells": [str(i), line]} for i, line in enumerate(_lines(r[1]) or ["-"], 1)]
    data = {
        "title": "DAILY PROGRESS REPORT", "subtitle": "Survey services", "number": number, "footer_title": "Daily Report",
        "meta": [("Report No.", number), ("Date", _d(r[0])), ("Day", f"#{r[16]}"),
                 ("Progress", f"{Decimal(r[5]).normalize():f}%" if r[5] is not None else "-")],
        "parties": [("Project", [r[9], r[8], r[12] or "-"]), ("Client", [r[11]]),
                    ("Conditions", [r[2] or "-", f"Vessel: {r[13] or '-'}"])],
        "sections": [
            {"heading": "Activities"},
            {"table": {"columns": ["No", "Description"], "widths": [10, None], "rows": activities, "center": (0,)}},
            {"table": {"columns": ["Working hours", "Standby hours", "Total"], "widths": [None, 59, 59],
                       "rows": [{"cells": [f"{Decimal(r[3]).normalize():f} h", f"{Decimal(r[4]).normalize():f} h",
                                           f"{Decimal(r[3] + r[4]).normalize():f} h"]}], "center": (0, 1, 2)}},
            {"box": {"title": "Issues / remarks", "lines": [(r[6] or "No issues reported.", {"size": 8.5})]}},
            {"box": {"title": "Personnel on site", "lines": [(x, {"size": 8.5}) for x in (_lines(r[14]) or ["-"])]}},
        ],
        "signatures": [("Prepared by (party chief)", r[7], "Date :"), ("Acknowledged by (client)", "", "Name, date & signature")],
    }
    return _respond(request, build_generic(_brand_code(r[10]), data), number, "survey_reports")


@business_permission_required("PROYEK", "LIHAT")
def survey_bast_document(request, project_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pr.kode_proyek, pr.nama, co.kode, kl.nama_perusahaan, COALESCE(kl.alamat, ''),
                       COALESCE(pr.lokasi_teks, ''), pr.tanggal_mulai_aktual, pr.tanggal_selesai_aktual,
                       pr.tanggal_mulai_rencana, pr.tanggal_selesai_rencana, pr.bast_tanggal,
                       COALESCE(pr.bast_penerima, ''), COALESCE(q.nomor, '-'), COALESCE(pg.nama, '')
                FROM proyek pr
                JOIN perusahaan co ON co.id = pr.perusahaan_id
                JOIN klien kl ON kl.id = pr.klien_id
                LEFT JOIN quotation q ON q.id = pr.quotation_id
                LEFT JOIN pengguna pg ON pg.id = pr.pic_internal_id
                WHERE pr.id = %s
                """,
                [project_id],
            )
            r = cursor.fetchone()
            if not r:
                raise Http404("Proyek tidak ditemukan.")
            cursor.execute(
                "SELECT judul, versi, diunggah_pada FROM deliverable_proyek WHERE proyek_id = %s ORDER BY diunggah_pada",
                [project_id],
            )
            deliverables = cursor.fetchall()
    except DatabaseError:
        messages.error(request, "Data proyek belum dapat dibaca.")
        return redirect("survey_deliverables")
    number = f"BAST/{r[0]}"
    start, end = r[6] or r[8], r[7] or r[9]
    rows = [{"cells": [str(i), d[0], d[1].title(), _d(timezone.localtime(d[2]).date())]} for i, d in enumerate(deliverables, 1)] or         [{"cells": ["–", "Deliverables as per scope of work", "Final", "-"]}]
    data = {
        "title": "HANDOVER CERTIFICATE", "subtitle": "Berita acara serah terima pekerjaan", "number": number,
        "footer_title": "BAST",
        "meta": [("BAST No.", number), ("Date", _d(r[10]) if r[10] else "-"), ("Project", r[0]), ("Quotation", r[12])],
        "parties": [("Client", [r[3], *_lines(r[4])[:2]]), ("Project", [r[1], r[5] or "-"]),
                    ("Execution period", [f"{_d(start)} – {_d(end)}"])],
        "sections": [
            {"text": "We hereby confirm that the survey work described below has been completed and the following "
                     "deliverables have been handed over to and accepted by the Client."},
            {"table": {"columns": ["No", "Deliverable", "Version", "Date"], "widths": [10, None, 26, 36],
                       "rows": rows, "center": (0, 2, 3)}},
            {"box": {"title": "Acceptance", "lines": [("With the signing of this certificate, the work is declared complete "
                                                       "and the final payment may be invoiced in accordance with the agreed terms.",
                                                       {"size": 8.5})]}},
        ],
        "signatures": [(f"For {BRANDS[_brand_code(r[2])].legal_name}", r[13] or "", "Project manager"),
                       ("Accepted by (client)", r[11], "Name, date & company stamp")],
        "signature_height": 24,
    }
    return _respond(request, build_generic(_brand_code(r[2]), data), number, "survey_deliverables")
