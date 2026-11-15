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
    BRANDS, build_delivery_order, build_invoice, build_quotation, default_terms,
)
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
                       q.jenis
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


@business_permission_required("SERAH_TERIMA", "LIHAT")
def sale_delivery_order(request, quotation_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pp.id, pp.nomor_pesanan, co.kode, kl.nama_perusahaan, COALESCE(kl.alamat, ''),
                       q.nomor, COALESCE((SELECT i.po_no FROM invoice i WHERE i.quotation_id = q.id
                                          ORDER BY i.id LIMIT 1), '-'),
                       COALESCE(bd.nama, ''), COALESCE(bd.email, ''),
                       st.tanggal, COALESCE(st.diterima_oleh, ''), COALESCE(pic.nama, ''),
                       COALESCE(st.catatan, ''), st.id
                FROM pesanan_penjualan pp
                JOIN quotation q ON q.id = pp.quotation_id
                JOIN perusahaan co ON co.id = pp.perusahaan_id
                JOIN klien kl ON kl.id = pp.klien_id
                LEFT JOIN pengguna bd ON bd.id = q.pic_pengguna_id
                LEFT JOIN LATERAL (
                    SELECT * FROM serah_terima_penjualan s WHERE s.pesanan_id = pp.id ORDER BY s.tanggal LIMIT 1
                ) st ON TRUE
                LEFT JOIN pengguna pic ON pic.id = st.diserahkan_oleh
                WHERE pp.quotation_id = %s
                """,
                [quotation_id],
            )
            o = cursor.fetchone()
            if not o:
                messages.error(request, "Barang untuk quotation ini belum dicatat terkirim.")
                return redirect("quotation_list")
            cursor.execute(
                """
                SELECT ppi.deskripsi_snapshot, COALESCE(u.nomor_seri, '-'), sti.kuantitas,
                       COALESCE(qi.satuan, 'unit'), COALESCE(u.kondisi, 'BAIK'), u.id
                FROM serah_terima_penjualan_item sti
                JOIN pesanan_penjualan_item ppi ON ppi.id = sti.pesanan_item_id
                LEFT JOIN quotation_item qi ON qi.id = ppi.quotation_item_id
                LEFT JOIN unit_alat u ON u.id = sti.unit_alat_id
                WHERE sti.serah_terima_id = %s
                ORDER BY sti.id
                """,
                [o[13]],
            )
            items = []
            for name, serial, qty, unit, condition, unit_id in cursor.fetchall():
                accessories = _accessories_for_unit(cursor, unit_id)
                items.append({"name": name, "details": [f"Incl. {accessories}"] if accessories else [],
                              "serial": serial, "qty": qty, "unit": unit,
                              "condition": CONDITION_EN.get(condition, condition.title())})
    except DatabaseError:
        messages.error(request, "Data pengiriman belum dapat dibaca dari database.")
        return redirect("quotation_list")

    company = _brand_code(o[2])
    number = "DO/" + (o[1][3:] if o[1].startswith("SO/") else o[1])
    data = {
        "number": number, "date": _d(o[9].date() if o[9] else date.today()),
        "reference": f"{o[5]} · {o[1]}", "po": o[6], "purpose": "Product delivery",
        "client": {"name": o[3], "attn": o[10] or "-"},
        "site_lines": _lines(o[4])[:2],
        "ship_via": f"Delivered {_d(o[9])}" if o[9] else "-",
        "ship_lines": _lines(o[12])[:2],
        "rep": {"name": o[7] or "Sales", "email": o[8] or "-", "phone": ""},
        "prepared_by": o[11], "delivered_by": "",
        "items": items,
    }
    return _respond(request, build_delivery_order(company, data), number, "quotation_list")
