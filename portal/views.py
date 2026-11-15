import json
import logging
import hashlib
import threading
import time
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.views import LoginView
from django.contrib import messages
from django.contrib.auth.hashers import make_password
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.core.cache import cache
from django.core.files.storage import default_storage
from django.db import DatabaseError, connection, transaction
from django.http import Http404, HttpResponse, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone

from .pdf import PdfConversionError, docx_to_pdf
from .forms import (
    AccessoryFormSet,
    EquipmentForm,
    InvoiceForm,
    ProjectForm,
    QuotationForm,
    QuotationItemFormSet,
    RentalCreateForm,
    RentalHandoverForm,
    RentalLocationForm,
    RoleAssignmentForm,
    SaleDeliveryForm,
)

logger = logging.getLogger(__name__)

QUOTATION_KIND_LABELS = {
    "RENTAL": "Rental alat",
    "PENJUALAN": "Penjualan produk",
    "JASA_SURVEY": "Jasa survey",
}
_place_search_lock = threading.Lock()
_place_search_last_request = 0.0


def business_permission_required(source, action):
    """Enforce the database role matrix for signed-in staff accounts."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={urllib.parse.quote(request.get_full_path(), safe='')}")
            if not request.user.is_staff:
                raise PermissionDenied
            required_action = action(request, *args, **kwargs) if callable(action) else action
            if request.user.is_superuser:
                return view(request, *args, **kwargs)
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM pengguna u
                            JOIN pengguna_peran up ON up.pengguna_id = u.id
                            JOIN peran p ON p.id = up.peran_id
                            JOIN izin_peran ip ON ip.peran_id = p.id
                            WHERE lower(u.email) = lower(%s) AND u.aktif
                              AND (
                                  (ip.sumber = %s AND ip.aksi = %s)
                                  OR (%s = 'LIHAT' AND ip.aksi = 'LIHAT'
                                      AND ip.sumber IN ('SEMUA_DATA', 'SEMUA_TRANSAKSI', 'SEMUA_LAPORAN'))
                              )
                        )
                        """,
                        [request.user.email, source, required_action, required_action],
                    )
                    permitted = cursor.fetchone()[0]
            except DatabaseError:
                return render(request, "portal/permission_denied.html", {
                    "database_error": True, "source": source, "action": required_action,
                }, status=503)
            if not permitted:
                return render(request, "portal/permission_denied.html", {
                    "database_error": False, "source": source, "action": required_action,
                }, status=403)
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


# Foto landing page. Untuk memakai foto perusahaan sendiri, simpan file JPG
# dengan nama yang sama di portal/static/portal/foto/ (mis. survey.jpg) —
# file lokal otomatis dipakai menggantikan foto contoh dari Unsplash.
LANDING_PHOTOS = {
    "hero": "https://images.unsplash.com/photo-1510601229148-d11d81569a97?auto=format&fit=crop&w=2000&q=70",
    "survey": "https://images.unsplash.com/photo-1582032951374-314efb7ccb76?auto=format&fit=crop&w=1200&q=70",
    "rental": "https://images.unsplash.com/photo-1563544389473-a4ff95d75926?auto=format&fit=crop&w=1200&q=70",
    "sales": "https://images.unsplash.com/photo-1573483387033-3121eaa077d7?auto=format&fit=crop&w=1200&q=70",
}


def _landing_photos():
    from django.contrib.staticfiles import finders
    from django.templatetags.static import static
    photos = {}
    for key, fallback in LANDING_PHOTOS.items():
        local = f"portal/foto/{key}.jpg"
        photos[key] = static(local) if finders.find(local) else fallback
    return photos


def landing(request):
    return render(request, "portal/landing.html", {"photos": _landing_photos()})


class StaffLoginView(LoginView):
    template_name = "portal/login.html"
    redirect_authenticated_user = True
    next_page = "dashboard"

    def form_valid(self, form):
        if not form.get_user().is_staff:
            form.add_error(None, "Akun ini belum memiliki akses untuk Oseana · Seanav Operations.")
            return self.form_invalid(form)
        return super().form_valid(form)


def role_management(request):
    if not request.user.is_authenticated:
        return redirect("login")
    if not request.user.is_superuser:
        raise PermissionDenied
    form = None
    database_error = None
    assignments = []
    try:
        form = RoleAssignmentForm(request.POST or None)
        if request.method == "POST" and form.is_valid():
            staff_user = get_user_model().objects.get(pk=form.cleaned_data["staff_user_id"])
            role_code = form.cleaned_data["role_code"]
            employee_name = staff_user.get_full_name() or staff_user.username
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT id FROM pengguna WHERE lower(email) = lower(%s) FOR UPDATE", [staff_user.email])
                    employee = cursor.fetchone()
                    if employee:
                        employee_id = employee[0]
                        cursor.execute("UPDATE pengguna SET nama = %s, aktif = TRUE WHERE id = %s", [employee_name, employee_id])
                    else:
                        cursor.execute(
                            "INSERT INTO pengguna (nama, email, password_hash, aktif) VALUES (%s, %s, %s, TRUE) RETURNING id",
                            [employee_name, staff_user.email, make_password(None)],
                        )
                        employee_id = cursor.fetchone()[0]
                    cursor.execute("SELECT id FROM peran WHERE kode = %s", [role_code])
                    role = cursor.fetchone()
                    if not role:
                        raise DatabaseError("Peran tidak tersedia di database.")
                    cursor.execute("DELETE FROM pengguna_peran WHERE pengguna_id = %s", [employee_id])
                    cursor.execute("INSERT INTO pengguna_peran (pengguna_id, peran_id) VALUES (%s, %s)", [employee_id, role[0]])
            messages.success(request, f"Peran {role_code.replace('_', ' ').title()} diberikan kepada {employee_name}.")
            return redirect("role_management")
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.nama, u.email, string_agg(p.nama, ', ' ORDER BY p.nama)
                FROM pengguna u
                JOIN pengguna_peran up ON up.pengguna_id = u.id
                JOIN peran p ON p.id = up.peran_id
                WHERE u.aktif
                GROUP BY u.id, u.nama, u.email
                ORDER BY u.nama
                """
            )
            assignments = [{"name": row[0], "email": row[1], "role": row[2]} for row in cursor.fetchall()]
    except DatabaseError:
        database_error = "Data role belum siap. Pastikan schema_awal.sql dan data peran sudah dimuat."
    return render(request, "portal/role_management.html", {
        "form": form, "assignments": assignments, "database_error": database_error,
    })


@business_permission_required("QUOTATION", "LIHAT")
def quotation_list(request):
    quotations = []
    database_error = None
    kind_filter = request.GET.get("jenis", "").upper()
    if kind_filter not in QUOTATION_KIND_LABELS:
        kind_filter = ""
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT q.id, p.kode, q.nomor, kl.nama_perusahaan, q.tanggal,
                       q.berlaku_sampai, q.status, q.total, q.subtotal,
                       q.jenis_pajak, q.tarif_pajak, q.total_pajak,
                       COALESCE(items.jumlah_item, 0), items.daftar_item,
                       EXISTS (SELECT 1 FROM rental r WHERE r.quotation_id = q.id),
                       EXISTS (SELECT 1 FROM invoice i WHERE i.quotation_id = q.id),
                       q.jenis,
                       EXISTS (SELECT 1 FROM pesanan_penjualan pp WHERE pp.quotation_id = q.id)
                FROM quotation q
                JOIN perusahaan p ON p.id = q.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(*) AS jumlah_item,
                           string_agg(qi.deskripsi_item || ' (' || trim_scale(qi.kuantitas)::text || ' ' || qi.satuan || ')',
                                      ', ' ORDER BY qi.id) AS daftar_item
                    FROM quotation_item qi WHERE qi.quotation_id = q.id
                ) items ON TRUE
                WHERE (%s = '' OR q.jenis = %s)
                ORDER BY q.dibuat_pada DESC, q.id DESC
                LIMIT 500
                """,
                [kind_filter, kind_filter],
            )
            quotations = [
                {
                    "id": row[0], "company": row[1], "number": row[2],
                    "client": row[3], "date": row[4], "valid_until": row[5],
                    "status": row[6], "total": row[7], "subtotal": row[8],
                    "tax_name": row[9], "tax_rate": row[10], "tax_amount": row[11],
                    "item_count": row[12], "items": row[13], "rental_created": row[14],
                    "invoice_created": row[15], "kind": row[16],
                    "kind_label": QUOTATION_KIND_LABELS.get(row[16], row[16]),
                    "delivered": row[17],
                }
                for row in cursor.fetchall()
            ]
    except DatabaseError:
        database_error = "Belum dapat membaca quotation. Pastikan skema quotation sudah tersedia di database."
    return render(
        request, "portal/quotation_list.html",
        {"quotations": quotations, "database_error": database_error,
         "kind_filter": kind_filter, "kind_choices": QUOTATION_KIND_LABELS.items()},
    )


@business_permission_required("QUOTATION", "LIHAT")
def quotation_document(request, quotation_id):
    """Generate a Word quotation document directly from stored quotation data."""
    try:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
        from docx.shared import Inches, Pt, RGBColor
    except ImportError:
        messages.error(request, "Generator DOCX belum terpasang. Jalankan pip install -r requirements.txt.")
        return redirect("quotation_list")

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT q.nomor, q.tanggal, q.berlaku_sampai, q.total, q.catatan,
                       p.nama_resmi, p.kode, kl.nama_perusahaan,
                       COALESCE(kl.alamat, ''), COALESCE(kl.email, ''),
                       COALESCE(kl.telepon, ''),
                       COALESCE((SELECT kk.nama FROM kontak_klien kk
                                 WHERE kk.klien_id = kl.id ORDER BY kk.utama DESC, kk.id LIMIT 1), ''),
                       COALESCE(bd.nama, ''), COALESCE(bd.email, ''),
                       q.subtotal, q.tarif_pajak, q.total_pajak, q.jenis_pajak, q.jenis
                FROM quotation q
                JOIN perusahaan p ON p.id = q.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                LEFT JOIN pengguna bd ON bd.id = q.pic_pengguna_id
                WHERE q.id = %s
                """,
                [quotation_id],
            )
            quote = cursor.fetchone()
            if not quote:
                raise Http404("Quotation tidak ditemukan.")
            cursor.execute(
                """
                SELECT qi.deskripsi_item, qi.kuantitas, qi.satuan, qi.harga_satuan,
                       qi.subtotal, qi.tanggal_mulai_rencana,
                       qi.tanggal_selesai_rencana, COALESCE(k.nama, ''),
                       COALESCE((
                           SELECT string_agg(accessory.item_text, E'\\n' ORDER BY accessory.item_text)
                           FROM (
                               SELECT DISTINCT trim_scale(pui.jumlah)::text || 'x ' || pai.nama_item AS item_text
                               FROM unit_alat u
                               JOIN paket_unit pu ON pu.unit_utama_id = u.id
                               JOIN paket_unit_item pui ON pui.paket_unit_id = pu.id
                               JOIN paket_alat_item pai ON pai.id = pui.paket_alat_item_id
                               WHERE u.model_alat_id = qi.model_alat_id
                           ) accessory
                       ), ''),
                       (SELECT u.foto_url FROM unit_alat u
                        WHERE u.model_alat_id = qi.model_alat_id
                          AND NULLIF(u.foto_url, '') IS NOT NULL
                        ORDER BY u.id LIMIT 1)
                FROM quotation_item qi
                LEFT JOIN model_alat m ON m.id = qi.model_alat_id
                LEFT JOIN kategori_alat k ON k.id = m.kategori_alat_id
                WHERE qi.quotation_id = %s
                ORDER BY qi.id
                """,
                [quotation_id],
            )
            quote_items = cursor.fetchall()
    except DatabaseError:
        messages.error(request, "Data quotation belum dapat dibaca dari database.")
        return redirect("quotation_list")

    template_names = {
        "QR": "Quotation Rental Oseana.docx",
        "SNV": "Quotation Rental Seanav.docx",
    }
    template_name = template_names.get(quote[6])
    if not template_name:
        messages.error(request, "Belum ada template quotation untuk perusahaan ini.")
        return redirect("quotation_list")
    template_path = _document_template(template_name)
    if not template_path.is_file():
        messages.error(request, "Template quotation Word tidak ditemukan di folder proyek.")
        return redirect("quotation_list")
    document = Document(template_path)
    if len(document.tables) < 3:
        messages.error(request, "Struktur template quotation tidak sesuai.")
        return redirect("quotation_list")

    date_text = quote[1].strftime("%d %B %Y") if quote[1] else "-"
    quote_kind = quote[18] or "RENTAL"
    period_word = {"RENTAL": "Rental", "JASA_SURVEY": "Survey"}.get(quote_kind, "Delivery")

    def quantity_text(quantity, unit, start, end):
        if start and end:
            days = (end - start).days + 1
            return f"{quantity:g} {unit} x {days} day{'s' if days != 1 else ''}"
        return f"{quantity:g} {unit}"

    def add_equipment_photo(cell, photo_path):
        """Embed the first available inventory photo for this quotation model."""
        if not photo_path:
            return
        try:
            with default_storage.open(photo_path, "rb") as photo_file:
                paragraph = cell.add_paragraph()
                paragraph.paragraph_format.space_before = Pt(4)
                paragraph.paragraph_format.space_after = Pt(2)
                paragraph.add_run().add_picture(photo_file, width=Inches(0.9))
        except Exception:
            # A missing optional photo must not prevent the quotation from being generated.
            return

    def set_cell_fill(cell, color):
        properties = cell._tc.get_or_add_tcPr()
        shading = properties.find(qn("w:shd"))
        if shading is None:
            shading = OxmlElement("w:shd")
            properties.append(shading)
        shading.set(qn("w:fill"), color)

    def format_cell(cell, *, color="263238", bold=False, size=9, align=None):
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for paragraph in cell.paragraphs:
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.0
            if align is not None:
                paragraph.alignment = align
            for run in paragraph.runs:
                run.font.name = "Aptos"
                run.font.size = Pt(size)
                run.font.bold = bold
                run.font.color.rgb = RGBColor.from_string(color)

    def compact_template_spacing():
        """Remove template spacer paragraphs between tables across company templates."""
        body = document._body._element
        children = list(body)
        kept_table_gaps = set()
        for index, element in enumerate(children):
            if not element.tag.endswith("}p"):
                continue
            paragraph_text = "".join(element.itertext()).strip()
            if paragraph_text:
                continue
            previous_index = index - 1
            while previous_index >= 0 and children[previous_index].tag.endswith("}p") and not "".join(children[previous_index].itertext()).strip():
                previous_index -= 1
            next_index = index + 1
            while next_index < len(children) and children[next_index].tag.endswith("}p") and not "".join(children[next_index].itertext()).strip():
                next_index += 1
            between_tables = (
                previous_index >= 0 and next_index < len(children)
                and children[previous_index].tag.endswith("}tbl")
                and children[next_index].tag.endswith("}tbl")
            )
            if between_tables:
                # Keep one intentional blank line between major table sections.
                gap_key = (previous_index, next_index)
                if gap_key not in kept_table_gaps:
                    kept_table_gaps.add(gap_key)
                    continue
                body.remove(element)

    def replace_cell_paragraph_text(cell, replacements):
        """Change wording while keeping the template's paragraph/list formatting."""
        for paragraph in cell.paragraphs:
            if not paragraph.runs:
                continue
            original = "".join(run.text for run in paragraph.runs)
            updated = original
            for old, new in replacements:
                updated = updated.replace(old, new)
            if updated != original:
                paragraph.runs[0].text = updated
                for run in paragraph.runs[1:]:
                    run.text = ""

    def set_summary_row(row, label, amount, *, fill="EEF4F7", text_color="263238", bold=False):
        row.cells[0].text = label
        row.cells[-1].text = amount
        for index, cell in enumerate(row.cells):
            set_cell_fill(cell, fill)
            format_cell(cell, color=text_color, bold=bold, size=9,
                        align=WD_ALIGN_PARAGRAPH.RIGHT)

    def add_summary_table(after_table, entries):
        summary = document.add_table(rows=len(entries), cols=2)
        summary.alignment = WD_TABLE_ALIGNMENT.RIGHT
        summary.autofit = False
        summary.columns[0].width = Inches(2.1)
        summary.columns[1].width = Inches(1.35)
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            border = OxmlElement(f"w:{edge}")
            if edge == "top":
                border.set(qn("w:val"), "single")
                border.set(qn("w:sz"), "6")
                border.set(qn("w:color"), "D7E0E5")
            else:
                border.set(qn("w:val"), "nil")
            borders.append(border)
        summary._tbl.tblPr.append(borders)
        body = document._body._element
        body.remove(summary._tbl)
        after_table._tbl.addnext(summary._tbl)
        for index, (label, amount, is_total) in enumerate(entries):
            row = summary.rows[index]
            row.cells[0].text = label
            row.cells[1].text = amount
            fill = "EAF1F5" if is_total else "FFFFFF"
            color = "17365D" if is_total else "37474F"
            for cell_index, cell in enumerate(row.cells):
                cell.width = summary.columns[cell_index].width
                set_cell_fill(cell, fill)
                format_cell(
                    cell, color=color, bold=is_total, size=10 if is_total else 9,
                    align=WD_ALIGN_PARAGRAPH.RIGHT if cell_index == 1 else WD_ALIGN_PARAGRAPH.LEFT,
                )
        return summary

    def add_note_after(after_table, note_text):
        if not note_text:
            return
        paragraph = document.add_paragraph()
        paragraph.paragraph_format.space_before = Pt(3)
        paragraph.paragraph_format.space_after = Pt(8)
        run = paragraph.add_run(note_text)
        run.font.name = "Aptos"
        run.font.size = Pt(8)
        run.font.italic = True
        run.font.color.rgb = RGBColor.from_string("667780")
        paragraph._p.getparent().remove(paragraph._p)
        after_table._tbl.addnext(paragraph._p)

    quotation_number = str(quote[0]).strip()
    company_code = str(quote[6]).strip()
    if quotation_number.upper().startswith(company_code.upper()):
        quotation_number = f"{company_code}{quotation_number[len(company_code):].lstrip('/ -')}"
    else:
        quotation_number = f"{company_code}{quotation_number.lstrip('/ -')}"
    if template_name in {"Quotation Rental Oseana.docx", "Quotation Rental Seanav.docx"}:
        account_table = document.tables[0]
        account_table.cell(1, 0).text = quote[7]
        account_table.cell(1, 1).text = date_text
        account_table.cell(1, 2).text = quotation_number
        account_table.cell(2, 0).text = "Prepared by"
        account_table.cell(2, 1).text = "Representative Email"
        account_table.cell(3, 0).text = quote[12] or "-"
        account_table.cell(3, 1).text = quote[13] or "-"

        bill_to = [quote[7]]
        if quote[11]:
            bill_to.append(f"Attn: {quote[11]}")
        if quote[8]:
            bill_to.extend(part.strip() for part in quote[8].replace("\r", "").split("\n") if part.strip())
        if quote[9]:
            bill_to.append(f"Email: {quote[9]}")
        if quote[10]:
            bill_to.append(f"Tel: {quote[10]}")
        bill_cell = document.tables[1].cell(1, 0)
        bill_cell.text = "\n".join(bill_to)
        ship_cell = document.tables[1].cell(1, 1)
        ship_cell.text = "\n".join(part.strip() for part in quote[8].replace("\r", "").split("\n") if part.strip()) if quote[8] else "Same as billing address"
        for address_cell in (bill_cell, ship_cell):
            address_cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for paragraph in address_cell.paragraphs:
                paragraph.paragraph_format.space_before = Pt(0)
                paragraph.paragraph_format.space_after = Pt(0)
                paragraph.paragraph_format.line_spacing = 1.0
                for run in paragraph.runs:
                    run.font.name = "Aptos"
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor.from_string("263238")

        items_table = document.tables[2]
        if len(items_table.rows) < 5 or len(items_table.rows[0].cells) < 6:
            messages.error(request, "Format template quotation tidak sesuai.")
            return redirect("quotation_list")
        info_table = items_table
        for cell_index, label in ((0, "Valid Until"), (1, "Valid Until"),
                                  (2, {"RENTAL": "Rental Starts", "JASA_SURVEY": "Survey Starts"}.get(quote_kind, "Delivery")), (3, "Commercial Terms"),
                                  (4, "Commercial Terms"), (5, "P.O.C.")):
            info_table.cell(0, cell_index).text = label
        start_dates = [item[5] for item in quote_items if item[5]]
        start_date = min(start_dates) if start_dates else None
        expiry_text = quote[2].strftime("%d %B %Y") if quote[2] else "-"
        start_text = start_date.strftime("%d %B %Y") if start_date else ("Upon PO confirmation" if quote_kind == "PENJUALAN" else "-")
        info_table.cell(1, 0).text = expiry_text
        info_table.cell(1, 1).text = expiry_text
        info_table.cell(1, 2).text = start_text
        terms_text = quote[4] or "As agreed by both parties."
        info_table.cell(1, 3).text = terms_text
        info_table.cell(1, 4).text = terms_text
        info_table.cell(1, 5).text = quote[11] or "-"

        # The supplied company templates reserve the final three rows for
        # subtotal, tax, and total. Keep those rows and replace only the
        # sample equipment rows above them.
        if len(items_table.rows) < 7:
            messages.error(request, "Format template quotation tidak sesuai.")
            return redirect("quotation_list")
        subtotal_xml = items_table.rows[-3]._tr
        exemplar = deepcopy(items_table.rows[4]._tr)
        for row in list(items_table.rows[4:-3]):
            row._tr.getparent().remove(row._tr)
        for index, item in enumerate(quote_items, 1):
            description, quantity, unit, unit_price, line_total, start, end, category, accessories, photo_path = item
            item_row = deepcopy(exemplar)
            subtotal_xml.addprevious(item_row)
            cells = items_table.rows[index + 3].cells
            days = (end - start).days + 1 if start and end else 1
            detail_lines = []
            if category:
                detail_lines.append(f"Category: {category}")
            if start and end:
                detail_lines.append(f"{period_word} period: {start:%d %b %Y} to {end:%d %b %Y}")
            if accessories:
                detail_lines.extend(["Accessories included:", *[f"- {line}" for line in accessories.splitlines()]])
            values = [
                (0, f"{index}."), (1, description),
                (2, "\n".join(detail_lines)),
                (3, quantity_text(quantity, unit, start, end)),
                (4, f"{Decimal(unit_price or 0):,.0f}"),
                (5, f"{Decimal(line_total or 0):,.0f}"),
            ]
            for cell_index, value in values:
                cells[cell_index].text = value
                cells[cell_index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for cell_index, cell in enumerate(cells):
                format_cell(
                    cell, size=9,
                    align=(WD_ALIGN_PARAGRAPH.CENTER if cell_index in (0, 3)
                           else WD_ALIGN_PARAGRAPH.RIGHT if cell_index in (4, 5)
                           else WD_ALIGN_PARAGRAPH.LEFT),
                )
            add_equipment_photo(cells[1], photo_path)

        subtotal_row, tax_row, total_row = items_table.rows[-3:]
        tax_amount = Decimal(quote[16] or 0)
        subtotal_amount = f"{Decimal(quote[14] or 0):,.0f}"
        if tax_amount:
            set_summary_row(subtotal_row, "SUBTOTAL PRICE (IDR)", subtotal_amount,
                            fill="17365D", text_color="FFFFFF")
            set_summary_row(
                tax_row, f"PPN {Decimal(quote[15] or 0).normalize():f}% (IDR)",
                f"{tax_amount:,.0f}", fill="EAF1F5", text_color="263238",
            )
            set_summary_row(
                total_row, "TOTAL INCLUDING PPN (IDR)",
                f"{Decimal(quote[3] or 0):,.0f}", fill="17365D", text_color="FFFFFF", bold=True,
            )
        else:
            tax_row._tr.getparent().remove(tax_row._tr)
            set_summary_row(
                subtotal_row, "SUBTOTAL PRICE (IDR)", subtotal_amount,
                fill="EAF1F5", text_color="263238",
            )
            set_summary_row(
                total_row, "TOTAL PRICE (IDR)", f"{Decimal(quote[3] or 0):,.0f}",
                fill="17365D", text_color="FFFFFF", bold=True,
            )

        terms_table = document.tables[3]
        if quote[6] == "QR":
            if len(terms_table.rows) > 2:
                terms_table.cell(2, 3).text = (
                    f"The prices quoted are firm and valid until {expiry_text}."
                    if quote[2] else "The prices quoted are firm as agreed by both parties."
                )
            if len(terms_table.rows) > 4:
                terms_table.cell(4, 3).text = terms_table.cell(4, 3).text.replace("CLIENT", quote[7])
            if len(terms_table.rows) > 3 and tax_amount:
                terms_table.cell(3, 3).text += " PPN is detailed separately in the quotation total."
        else:
            if len(terms_table.rows) > 1:
                replacements = [
                    ("CLIENT\uFFFDs", f"{quote[7]}'s"),
                    ("CLIENT", quote[7]),
                    ("SUPPLIER", quote[5]),
                ]
                if tax_amount:
                    replacements.append((
                        "Prices in this quotation exclusive PPN Tax",
                        f"This quotation includes PPN at {Decimal(quote[15] or 0).normalize():f}% as detailed in the total",
                    ))
                replace_cell_paragraph_text(terms_table.cell(1, 0), replacements)
            if terms_table.rows:
                title_cell = terms_table.cell(0, 0)
                replace_cell_paragraph_text(title_cell, [("erms & Condition :", "Terms & Condition:")])
    elif quote[6] == "QR":
        header = document.tables[0]
        header.cell(1, 2).text = quote[7]
        header.cell(1, 5).text = quotation_number
        client_lines = [quote[8] or ""]
        if quote[11]:
            client_lines.extend(["", f"Attn: {quote[11]}"])
        if quote[10]:
            client_lines.append(f"Tel: {quote[10]}")
        if quote[9]:
            client_lines.append(f"Email: {quote[9]}")
        header.cell(2, 2).text = "\n".join(client_lines).strip()
        header.cell(2, 5).text = date_text
        format_cell(header.cell(0, 5), color="17365D", bold=True, size=15,
                    align=WD_ALIGN_PARAGRAPH.RIGHT)
        for cell in (header.cell(1, 0), header.cell(2, 0)):
            set_cell_fill(cell, "EAF3F7")
            format_cell(cell, color="17365D", bold=True, size=10,
                        align=WD_ALIGN_PARAGRAPH.CENTER)
        format_cell(header.cell(1, 2), color="17365D", bold=True, size=10)
        format_cell(header.cell(2, 2), color="37474F", size=9)
        for cell in (header.cell(1, 5), header.cell(2, 5)):
            set_cell_fill(cell, "EAF3F7")
            format_cell(cell, color="17365D", bold=True, size=10,
                        align=WD_ALIGN_PARAGRAPH.CENTER)
        if quote[2] and len(document.tables) > 3 and len(document.tables[3].rows) > 1:
            document.tables[3].cell(1, 3).text = (
                f"The prices quoted are firm and will remain valid until {quote[2]:%d %B %Y}."
            )

        items_table = document.tables[2]
        for header_cell in items_table.rows[0].cells:
            set_cell_fill(header_cell, "17365D")
            format_cell(header_cell, color="FFFFFF", bold=True, size=9,
                        align=WD_ALIGN_PARAGRAPH.CENTER)
        for category_cell in items_table.rows[1].cells:
            set_cell_fill(category_cell, "EEF2F4")
            format_cell(category_cell, color="17365D", bold=True, size=9)
        total_xml = items_table.rows[-2]._tr
        exemplar = deepcopy(items_table.rows[2]._tr)
        for row in list(items_table.rows[1:-2]):
            row._tr.getparent().remove(row._tr)
        for index, item in enumerate(quote_items, 1):
            description, quantity, unit, unit_price, subtotal, start, end, category, accessories, photo_path = item
            item_row = deepcopy(exemplar)
            total_xml.addprevious(item_row)
            cells = items_table.rows[index].cells
            detail_lines = [description]
            if category:
                detail_lines.append(f"Category: {category}")
            if start and end:
                days = (end - start).days + 1
                detail_lines.append(f"{period_word} period: {start:%d %b %Y} to {end:%d %b %Y} ({days} day{'s' if days != 1 else ''})")
            if accessories:
                detail_lines.extend(["Include:", *[f"- {line.split('x ', 1)[-1]}" for line in accessories.splitlines()]])
            values = [
                (0, f"{index}."), (1, f"{quantity:g} {unit}"),
                (2, "\n".join(detail_lines)), (4, f"{Decimal(unit_price or 0):,.0f}"),
                (5, f"{Decimal(subtotal or 0):,.0f}"),
            ]
            for cell_index, value in values:
                cells[cell_index].text = value
                cells[cell_index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for cell_index, cell in enumerate(cells):
                format_cell(
                    cell, size=9,
                    align=(WD_ALIGN_PARAGRAPH.CENTER if cell_index in (0, 1)
                           else WD_ALIGN_PARAGRAPH.RIGHT if cell_index in (4, 5)
                           else WD_ALIGN_PARAGRAPH.LEFT),
                )
            add_equipment_photo(cells[2], photo_path)
        total_row = items_table.rows[-2]
        note_row = items_table.rows[-1]
        tax_amount = Decimal(quote[16] or 0)
        tax_rate = Decimal(quote[15] or 0).normalize()
        total_row._tr.getparent().remove(total_row._tr)
        note_row._tr.getparent().remove(note_row._tr)
        if tax_amount:
            summary_entries = [
                ("Subtotal (IDR)", f"{Decimal(quote[14] or 0):,.0f}", False),
                (f"{quote[17] or 'PPN'} {tax_rate:f}% (IDR)", f"{tax_amount:,.0f}", False),
                ("TOTAL INCLUDING PPN (IDR)", f"{Decimal(quote[3] or 0):,.0f}", True),
            ]
            notes = f"Notes: {quote[4]}" if quote[4] else ""
        else:
            summary_entries = [("TOTAL PRICE (IDR)", f"{Decimal(quote[3] or 0):,.0f}", True)]
            notes = "Prices exclude PPN."
            if quote[4]:
                notes = f"{notes} Notes: {quote[4]}"
        summary_table = add_summary_table(items_table, summary_entries)
        add_note_after(summary_table, notes)
    else:
        header = document.tables[0]
        header.cell(1, 0).text = quote[7]
        header.cell(1, 1).text = quote[1].strftime("%d/%m/%Y") if quote[1] else "-"
        header.cell(1, 2).text = quotation_number
        header.cell(3, 0).text = quote[12] or "-"
        header.cell(3, 1).text = quote[13] or "-"
        bill_to = [quote[7], quote[8] or ""]
        if quote[9]:
            bill_to.append(quote[9])
        if quote[10]:
            bill_to.append(quote[10])
        document.tables[1].cell(1, 0).text = "\n".join(line for line in bill_to if line)
        document.tables[1].cell(1, 1).text = quote[8] or "-"
        terms_table = document.tables[2]
        terms_table.cell(1, 0).text = quote[2].strftime("%d/%m/%Y") if quote[2] else "-"
        terms_table.cell(1, 5).text = quote[11] or "-"
        items_table = terms_table
        for header_cell in items_table.rows[2].cells:
            set_cell_fill(header_cell, "17365D")
            format_cell(header_cell, color="FFFFFF", bold=True, size=9,
                        align=WD_ALIGN_PARAGRAPH.CENTER)
        subtotal_xml = items_table.rows[-1]._tr
        exemplar = deepcopy(items_table.rows[4]._tr)
        for row in list(items_table.rows[4:-1]):
            row._tr.getparent().remove(row._tr)
        for index, item in enumerate(quote_items, 1):
            description, quantity, unit, unit_price, subtotal, start, end, category, accessories, photo_path = item
            item_row = deepcopy(exemplar)
            subtotal_xml.addprevious(item_row)
            cells = items_table.rows[index + 3].cells
            days = (end - start).days + 1 if start and end else 1
            detail_lines = [description]
            if category:
                detail_lines.append(f"Category: {category}")
            if start and end:
                detail_lines.append(f"{period_word} period: {start:%d/%m/%Y} to {end:%d/%m/%Y}")
            if accessories:
                detail_lines.extend(["Include:", *[f"- {line.split('x ', 1)[-1]}" for line in accessories.splitlines()]])
            values = [
                (0, f"{index}."), (1, "\n".join(detail_lines)),
                (3, quantity_text(quantity, unit, start, end)),
                (4, f"{Decimal(unit_price or 0):,.0f}"),
                (5, f"{Decimal(subtotal or 0):,.0f}"),
            ]
            for cell_index, value in values:
                cells[cell_index].text = value
                cells[cell_index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            for cell_index, cell in enumerate(cells):
                format_cell(
                    cell, size=9,
                    align=(WD_ALIGN_PARAGRAPH.CENTER if cell_index in (0, 3)
                           else WD_ALIGN_PARAGRAPH.RIGHT if cell_index in (4, 5)
                           else WD_ALIGN_PARAGRAPH.LEFT),
                )
            # The SNV template's example row has a product-name placeholder in Description.
            # Clear it so the cell contains only the inventory photo for equipment rows.
            cells[2].text = ""
            add_equipment_photo(cells[2], photo_path)
        subtotal_row = items_table.rows[-1]
        tax_amount = Decimal(quote[16] or 0)
        if tax_amount:
            tax_xml = deepcopy(subtotal_row._tr)
            grand_xml = deepcopy(subtotal_row._tr)
            subtotal_row._tr.addnext(tax_xml)
            tax_xml.addnext(grand_xml)
            set_summary_row(items_table.rows[-3], "SUBTOTAL (IDR)", f"{Decimal(quote[14] or 0):,.0f}")
            set_summary_row(
                items_table.rows[-2],
                f"{quote[17] or 'PPN'} {Decimal(quote[15] or 0).normalize():f}% (IDR)",
                f"{tax_amount:,.0f}", fill="F5F8FA",
            )
            set_summary_row(
                items_table.rows[-1], "TOTAL INCLUDING PPN (IDR)",
                f"{Decimal(quote[3] or 0):,.0f}", fill="17365D", text_color="FFFFFF", bold=True,
            )
        else:
            set_summary_row(
                subtotal_row, "TOTAL PRICE (IDR)", f"{Decimal(quote[3] or 0):,.0f}",
                fill="17365D", text_color="FFFFFF", bold=True,
            )

    compact_template_spacing()
    output = BytesIO()
    document.save(output)
    safe_number = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(quote[0]))
    if request.GET.get("format", "").lower() == "pdf":
        try:
            pdf_bytes = docx_to_pdf(output.getvalue(), f"{safe_number}_quotation")
        except PdfConversionError as exc:
            logger.error("PDF quotation %s gagal: %s", quote[0], exc)
            messages.error(request, "PDF belum berhasil dibuat. Unduh versi DOCX, atau pastikan Microsoft Word "
                                    "(Windows) / LibreOffice (server) terpasang.")
            return redirect("quotation_list")
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{safe_number}_quotation.pdf"'
        return response

    response = HttpResponse(
        output.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = f'attachment; filename="{safe_number}_quotation.docx"'
    return response


@business_permission_required("QUOTATION", "BUAT")
def quotation_create(request):
    database_error = None
    try:
        initial_kind = request.GET.get("jenis", "RENTAL").upper()
        form = QuotationForm(
            request.POST or None, user_email=request.user.email,
            initial={"kind": initial_kind if initial_kind in QUOTATION_KIND_LABELS else "RENTAL"},
        )
    except DatabaseError:
        form = None
        database_error = "Belum dapat membaca daftar perusahaan dari database."
    item_formset = QuotationItemFormSet(request.POST or None, prefix="items")
    form_valid = form.is_valid() if request.method == "POST" and form else False
    items_valid = item_formset.is_valid() if request.method == "POST" else False

    if request.method == "POST" and form_valid and items_valid:
        data = form.cleaned_data
        raw_items = [
            item.cleaned_data for item in item_formset.forms
            if (item.cleaned_data.get("equipment_id") or item.cleaned_data.get("description"))
            and not item.cleaned_data.get("DELETE")
        ]
        equipment_catalog = {
            int(item["id"]): item for item in item_formset.equipment_catalog
        }
        kind = data.get("kind") or "RENTAL"
        items = []
        for raw_item in raw_items:
            equipment_id = int(raw_item["equipment_id"]) if raw_item.get("equipment_id") else None
            equipment = equipment_catalog.get(equipment_id) if equipment_id else None
            if equipment_id and not equipment:
                form.add_error(None, "Alat tidak tersedia di katalog. Muat ulang halaman dan pilih kembali.")
                break
            if equipment:
                unit_price = raw_item["unit_price"]
                description = equipment["name"]
                model_id = equipment_id
                unit = "unit"
            else:
                description = raw_item["description"]
                unit_price = raw_item["unit_price"]
                model_id = None
                unit = raw_item["unit"] or "unit"
            days = raw_item.get("days")
            if kind == "RENTAL" and not days:
                form.add_error(None, f"Isi jumlah hari rental untuk item \"{description}\".")
                break
            if kind == "PENJUALAN":
                days = None
            multiplier = days or 1
            start_date = None
            end_date = None
            if days and (data.get("rental_start") or kind == "JASA_SURVEY"):
                start_date = data.get("rental_start") or data["date"]
                end_date = start_date + timedelta(days=days - 1)
            items.append({
                "model_id": model_id,
                "description": description,
                "quantity": raw_item["quantity"],
                "days": days,
                "unit": unit,
                "unit_price": unit_price,
                "subtotal": raw_item["quantity"] * unit_price * multiplier,
                "start_date": start_date,
                "end_date": end_date,
            })
        subtotal = sum((item["subtotal"] for item in items), Decimal("0")).quantize(Decimal("0.01"))
        tax_rate = Decimal("11.0000") if data["apply_tax"] else Decimal("0.0000")
        tax_amount = (subtotal * tax_rate / Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        grand_total = subtotal + tax_amount
        if form.non_field_errors():
            return render(
                request, "portal/quotation_form.html",
                {"form": form, "item_formset": item_formset,
                 "database_error": database_error,
                 "equipment_catalog": item_formset.equipment_catalog,
                 "catalog_error": item_formset.catalog_error},
            )
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM klien WHERE lower(nama_perusahaan) = lower(%s)",
                        [data["client_name"]],
                    )
                    client = cursor.fetchone()
                    if client:
                        client_id = client[0]
                    else:
                        cursor.execute(
                            """
                            INSERT INTO klien (nama_perusahaan, alamat, email, telepon)
                            VALUES (%s, %s, %s, %s) RETURNING id
                            """,
                            [data["client_name"], data["client_address"] or None,
                             data["client_email"] or None, data["client_phone"] or None],
                        )
                        client_id = cursor.fetchone()[0]
                    if client:
                        cursor.execute(
                            """
                            UPDATE klien
                            SET alamat = COALESCE(NULLIF(%s, ''), alamat),
                                email = COALESCE(NULLIF(%s, ''), email),
                                telepon = COALESCE(NULLIF(%s, ''), telepon)
                            WHERE id = %s
                            """,
                            [data["client_address"], data["client_email"],
                             data["client_phone"], client_id],
                        )
                    contact_name = (data["client_contact"] or "").strip()
                    if contact_name:
                        cursor.execute(
                            """
                            SELECT id FROM kontak_klien
                            WHERE klien_id = %s AND lower(nama) = lower(%s)
                            ORDER BY utama DESC, id LIMIT 1
                            """,
                            [client_id, contact_name],
                        )
                        contact = cursor.fetchone()
                        cursor.execute(
                            "UPDATE kontak_klien SET utama = FALSE WHERE klien_id = %s",
                            [client_id],
                        )
                        if contact:
                            cursor.execute(
                                "UPDATE kontak_klien SET utama = TRUE WHERE id = %s",
                                [contact[0]],
                            )
                        else:
                            cursor.execute(
                                "INSERT INTO kontak_klien (klien_id, nama, utama) VALUES (%s, %s, TRUE)",
                                [client_id, contact_name],
                            )

                    user_email = (request.user.email or "").strip().lower()
                    if not user_email:
                        user_email = f"{request.user.get_username()}@local.invalid"
                    cursor.execute(
                        "SELECT id FROM pengguna WHERE lower(email) = lower(%s)", [user_email]
                    )
                    user = cursor.fetchone()
                    if user:
                        user_id = user[0]
                    else:
                        cursor.execute(
                            """
                            INSERT INTO pengguna (nama, email, password_hash)
                            VALUES (%s, %s, %s) RETURNING id
                            """,
                            [request.user.get_full_name() or request.user.get_username(),
                             user_email, make_password(None)],
                        )
                        user_id = cursor.fetchone()[0]

                    cursor.execute(
                        "SELECT id FROM pengguna WHERE id = %s AND aktif",
                        [data["representative_id"]],
                    )
                    representative = cursor.fetchone()
                    if not representative:
                        raise DatabaseError("Selected quotation representative is no longer active")
                    representative_id = representative[0]

                    cursor.execute(
                        """
                        INSERT INTO quotation
                            (perusahaan_id, nomor, jenis, klien_id, tanggal, berlaku_sampai,
                             pic_pengguna_id, status, subtotal, jenis_pajak, tarif_pajak,
                             total_pajak, total, catatan, dibuat_oleh)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, 'DRAFT', %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        [data["company_id"], data["number"], kind, client_id, data["date"],
                         data["valid_until"], representative_id, subtotal,
                         "PPN" if data["apply_tax"] else None, tax_rate, tax_amount, grand_total,
                         data["notes"] or None, user_id],
                    )
                    quotation_id = cursor.fetchone()[0]
                    for item in items:
                        cursor.execute(
                            """
                            INSERT INTO quotation_item
                                (quotation_id, model_alat_id, deskripsi_item, kuantitas, satuan,
                                 harga_satuan, subtotal, tanggal_mulai_rencana,
                                 tanggal_selesai_rencana)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            [quotation_id, item["model_id"], item["description"], item["quantity"],
                             item["unit"], item["unit_price"], item["subtotal"],
                             item["start_date"], item["end_date"]],
                        )
            messages.success(request, f"Draft quotation {QUOTATION_KIND_LABELS.get(kind, kind).lower()} berhasil dibuat.")
            return redirect("quotation_list")
        except DatabaseError:
            form.add_error(None, "Quotation belum tersimpan. Periksa nomor quotation dan database.")

    return render(
        request, "portal/quotation_form.html",
        {"form": form, "item_formset": item_formset, "database_error": database_error,
         "equipment_catalog": item_formset.equipment_catalog,
         "catalog_error": item_formset.catalog_error},
    )


@business_permission_required("INVOICE", "LIHAT")
def invoice_list(request):
    invoices = []
    database_error = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT i.id, i.quotation_id, co.kode, i.nomor, kl.nama_perusahaan,
                       i.tanggal, i.jatuh_tempo, i.status, i.subtotal, i.total,
                       i.jenis_pajak, i.tarif_pajak, i.total_pajak, q.nomor,
                       EXISTS (SELECT 1 FROM rental r WHERE r.invoice_id = i.id), q.jenis
                FROM invoice i
                JOIN quotation q ON q.id = i.quotation_id
                JOIN perusahaan co ON co.id = i.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                ORDER BY i.dibuat_pada DESC, i.id DESC
                LIMIT 500
                """
            )
            invoices = [
                {
                    "id": row[0], "quotation_id": row[1], "company": row[2],
                    "number": row[3], "client": row[4], "date": row[5],
                    "due_date": row[6], "status": row[7], "subtotal": row[8],
                    "total": row[9], "tax_name": row[10], "tax_rate": row[11],
                    "tax_amount": row[12], "quotation_number": row[13],
                    "rental_created": row[14], "kind": row[15],
                    "kind_label": QUOTATION_KIND_LABELS.get(row[15], row[15]),
                }
                for row in cursor.fetchall()
            ]
    except DatabaseError:
        database_error = "Belum dapat membaca daftar invoice. Terapkan migrasi invoice dan periksa database."
    return render(request, "portal/invoice_list.html", {
        "invoices": invoices, "database_error": database_error,
    })


@business_permission_required("INVOICE", "BUAT")
def invoice_create(request, quotation_id):
    quote = None
    database_error = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT q.id, q.perusahaan_id, co.kode, q.nomor, q.tanggal,
                       q.subtotal, q.jenis_pajak, q.tarif_pajak, q.total_pajak,
                       q.total, q.pic_pengguna_id, kl.nama_perusahaan
                FROM quotation q
                JOIN perusahaan co ON co.id = q.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                WHERE q.id = %s
                  AND q.status = 'DITERIMA_KLIEN'
                  AND NOT EXISTS (SELECT 1 FROM invoice i WHERE i.quotation_id = q.id)
                """,
                [quotation_id],
            )
            quote = cursor.fetchone()
    except DatabaseError:
        database_error = "Belum dapat membaca quotation. Periksa database."

    if not quote and not database_error:
        messages.error(request, "Invoice hanya dapat dibuat dari quotation yang disetujui dan belum memiliki invoice.")
        return redirect("quotation_list")

    initial = {}
    if quote:
        initial = {
            "number": f"INV/{quote[3]}",
            "date": date.today(),
        }
    form = InvoiceForm(request.POST or None, initial=initial)
    if request.method == "POST" and quote and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT q.perusahaan_id, q.status, q.subtotal, q.jenis_pajak,
                               q.tarif_pajak, q.total_pajak, q.total, q.pic_pengguna_id
                        FROM quotation q
                        WHERE q.id = %s
                        FOR UPDATE
                        """,
                        [quotation_id],
                    )
                    locked_quote = cursor.fetchone()
                    if not locked_quote or locked_quote[1] != "DITERIMA_KLIEN":
                        form.add_error(None, "Quotation tidak lagi berstatus disetujui.")
                    else:
                        cursor.execute(
                            "SELECT 1 FROM invoice WHERE quotation_id = %s", [quotation_id]
                        )
                        if cursor.fetchone():
                            form.add_error(None, "Quotation ini sudah memiliki invoice.")
                        else:
                            cursor.execute(
                                """
                                INSERT INTO invoice
                                    (quotation_id, perusahaan_id, nomor, tanggal, jatuh_tempo,
                                     po_no, subtotal, jenis_pajak, tarif_pajak, total_pajak,
                                     total, syarat_pembayaran, catatan, dibuat_oleh)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                RETURNING id
                                """,
                                [quotation_id, locked_quote[0], data["number"], data["date"],
                                 data["due_date"], data["po_number"] or None,
                                 locked_quote[2], locked_quote[3], locked_quote[4],
                                 locked_quote[5], locked_quote[6], data["payment_terms"] or None,
                                 data["notes"] or None, locked_quote[7]],
                            )
                            invoice_id = cursor.fetchone()[0]
                            messages.success(request, "Draft invoice dibuat dari quotation yang disetujui.")
                            return redirect("invoice_list")
        except DatabaseError:
            form.add_error(None, "Invoice belum tersimpan. Periksa nomor invoice dan database.")
    return render(request, "portal/invoice_form.html", {
        "form": form, "quote": quote, "database_error": database_error,
    })


@business_permission_required("INVOICE", "UBAH")
def invoice_status(request, invoice_id, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    transitions = {
        "terbitkan": ("DRAFT", "DITERBITKAN"),
        "bayar": ("DITERBITKAN", "DIBAYAR"),
        "batalkan": ("DRAFT", "DIBATALKAN"),
    }
    if action not in transitions:
        raise Http404("Aksi invoice tidak ditemukan.")
    old_status, new_status = transitions[action]
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT status FROM invoice WHERE id = %s FOR UPDATE", [invoice_id])
                row = cursor.fetchone()
                if not row:
                    raise Http404("Invoice tidak ditemukan.")
                if row[0] != old_status:
                    messages.error(request, "Status invoice sudah berubah; muat ulang daftar.")
                elif action == "batalkan":
                    cursor.execute("SELECT EXISTS (SELECT 1 FROM rental WHERE invoice_id = %s)", [invoice_id])
                    if cursor.fetchone()[0]:
                        messages.error(request, "Invoice tidak dapat dibatalkan karena rental sudah dibuat.")
                    else:
                        cursor.execute("UPDATE invoice SET status = %s WHERE id = %s", [new_status, invoice_id])
                        messages.success(request, "Invoice dibatalkan.")
                else:
                    timestamp_column = "diterbitkan_pada" if new_status == "DITERBITKAN" else "dibayar_pada"
                    cursor.execute(
                        f"UPDATE invoice SET status = %s, {timestamp_column} = now() WHERE id = %s",
                        [new_status, invoice_id],
                    )
                    messages.success(request, "Invoice berhasil diterbitkan." if new_status == "DITERBITKAN" else "Pembayaran invoice dicatat lunas.")
    except DatabaseError:
        messages.error(request, "Perubahan status invoice belum tersimpan.")
    return redirect("invoice_list")


@business_permission_required("INVOICE", "LIHAT")
def invoice_document(request, invoice_id):
    try:
        from docx import Document
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Pt, Twips
    except ImportError:
        messages.error(request, "Generator DOCX belum terpasang. Jalankan pip install -r requirements.txt.")
        return redirect("invoice_list")

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT i.id, i.nomor, i.tanggal, i.jatuh_tempo, i.po_no, i.status,
                       i.subtotal, i.jenis_pajak, i.tarif_pajak, i.total_pajak, i.total,
                       i.syarat_pembayaran, i.catatan, q.id, q.nomor, co.kode,
                       kl.nama_perusahaan, COALESCE(kl.alamat, ''),
                       COALESCE(kl.email, ''), COALESCE(kl.telepon, ''),
                       COALESCE((SELECT kk.nama FROM kontak_klien kk WHERE kk.klien_id = kl.id
                                 ORDER BY kk.utama DESC, kk.id LIMIT 1), ''),
                       COALESCE(pic.nama, ''), COALESCE(pic.email, ''), q.jenis
                FROM invoice i
                JOIN quotation q ON q.id = i.quotation_id
                JOIN perusahaan co ON co.id = i.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                LEFT JOIN pengguna pic ON pic.id = q.pic_pengguna_id
                WHERE i.id = %s
                """,
                [invoice_id],
            )
            invoice = cursor.fetchone()
            if not invoice:
                raise Http404("Invoice tidak ditemukan.")
            cursor.execute(
                """
                SELECT qi.deskripsi_item, qi.kuantitas, qi.satuan, qi.harga_satuan,
                       qi.subtotal, qi.tanggal_mulai_rencana, qi.tanggal_selesai_rencana
                FROM quotation_item qi WHERE qi.quotation_id = %s ORDER BY qi.id
                """,
                [invoice[13]],
            )
            items = cursor.fetchall()
    except DatabaseError:
        messages.error(request, "Invoice belum dapat dibaca dari database.")
        return redirect("invoice_list")

    template_name = "Invoince Oseana.docx" if invoice[15] == "QR" else "Invoince Seanav.docx"
    template_path = _document_template(template_name)
    if not template_path.is_file():
        messages.error(request, "Template invoice Word tidak ditemukan di folder proyek.")
        return redirect("invoice_list")
    document = Document(template_path)
    tables = document.tables
    if len(tables) < 4 or len(tables[1].rows) < 4:
        messages.error(request, "Struktur template invoice tidak sesuai.")
        return redirect("invoice_list")

    def set_invoice_row(row, label, amount, *, bold=False):
        row.cells[0].text = label
        row.cells[-1].text = amount
        for cell_index, cell in enumerate(row.cells):
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                paragraph.paragraph_format.space_before = 0
                paragraph.paragraph_format.space_after = 0
                for run in paragraph.runs:
                    run.bold = bold
                    run.font.name = "Aptos"
                    run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Aptos")

    def format_cell(cell, *, align=WD_ALIGN_PARAGRAPH.LEFT, no_wrap=False, preserve_list=False):
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for paragraph in cell.paragraphs:
            if not preserve_list:
                paragraph.style = document.styles["Normal"]
            paragraph.alignment = align
            paragraph.paragraph_format.space_before = Pt(0)
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.paragraph_format.line_spacing = 1.0
            paragraph.paragraph_format.left_indent = Twips(0)
            paragraph.paragraph_format.right_indent = Twips(0)
            paragraph.paragraph_format.first_line_indent = Twips(0)
            paragraph.paragraph_format.keep_together = True
            if not preserve_list and paragraph._p.pPr is not None:
                num_pr = paragraph._p.pPr.find(qn("w:numPr"))
                if num_pr is not None:
                    paragraph._p.pPr.remove(num_pr)
            for run in paragraph.runs:
                run.font.name = "Aptos"
                run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Aptos")
        if no_wrap and not cell._tc.tcPr.find("w:noWrap", namespaces=cell._tc.nsmap):
            cell._tc.get_or_add_tcPr().append(OxmlElement("w:noWrap"))

    def format_idr(value):
        return f"{Decimal(value or 0):,.0f}".replace(",", ".")

    if len(document.paragraphs) > 1:
        document.paragraphs[1].text = invoice[1]
    invoice_kind = invoice[23] or "RENTAL"
    for p in document.paragraphs:
        if p.text.strip() == "Sale for:":
            p.text = {"RENTAL": "Rental for:", "JASA_SURVEY": "Service for:"}.get(invoice_kind, "Sale for:")

    quote_table = tables[0]
    # Keep the template's six-column grid, but close excess gaps around the
    # separator columns so labels, colons, and values read as one aligned pair.
    quote_table.autofit = False
    invoice_info_widths = (1200, 180, 4350, 1600, 180, 2950)
    for column, width in zip(quote_table.columns, invoice_info_widths):
        column.width = Twips(width)
    for row in quote_table.rows:
        for cell, width in zip(row.cells, invoice_info_widths):
            cell.width = Twips(width)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for row_index in (0, 2, 3, 5):
        row = quote_table.rows[row_index]
        for label_index, separator_index, value_index in ((0, 1, 2), (3, 4, 5)):
            for index, alignment in (
                (label_index, WD_ALIGN_PARAGRAPH.LEFT),
                (separator_index, WD_ALIGN_PARAGRAPH.CENTER),
                (value_index, WD_ALIGN_PARAGRAPH.LEFT),
            ):
                for paragraph in row.cells[index].paragraphs:
                    paragraph.alignment = alignment
                    paragraph.paragraph_format.space_before = Pt(0)
                    paragraph.paragraph_format.space_after = Pt(0)
    quote_table.cell(0, 2).text = invoice[16]
    english_months = (
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    )

    def format_english_date(value):
        return f"{value.day} {english_months[value.month - 1]} {value.year}"

    invoice_date = format_english_date(invoice[2])
    due_line = f"\nDue: {format_english_date(invoice[3])}" if invoice[3] else ""
    quote_table.cell(0, 5).text = invoice_date + due_line
    address_lines = [line.strip() for line in (invoice[17] or "").replace("\r", "").split("\n") if line.strip()]
    if invoice[18]:
        address_lines.append(f"Email: {invoice[18]}")
    if invoice[19]:
        address_lines.append(f"Tel: {invoice[19]}")
    quote_table.cell(2, 2).text = "\n".join(address_lines) or "-"
    quote_table.cell(2, 5).text = {"RENTAL": "For Rent", "JASA_SURVEY": "Survey Service"}.get(invoice_kind, "For Sale")
    quote_table.cell(3, 5).text = invoice[4] or "-"
    quote_table.cell(5, 2).text = invoice[20] or "-"
    quote_table.cell(5, 5).text = invoice[14]

    items_table = tables[1]
    if len(items_table.rows) >= 9:
        column_widths = [700, 1800, 3450, 2200, 2292]
        items_table.autofit = False
        for column, width in zip(items_table.columns, column_widths):
            column.width = Twips(width)
        summary_anchor = items_table.rows[-3]._tr
        exemplar = deepcopy(items_table.rows[1]._tr)
        for row in list(items_table.rows[1:-3]):
            row._tr.getparent().remove(row._tr)
        for index, item in enumerate(items, 1):
            description, quantity, unit, unit_price, line_total, start, end = item
            row_xml = deepcopy(exemplar)
            summary_anchor.addprevious(row_xml)
            row = items_table.rows[index]
            row.cells[0].text = f"{index}."
            if start and end:
                days = (end - start).days + 1
                row.cells[1].text = f"{quantity:g} {unit} x {days} day{'s' if days != 1 else ''}"
            else:
                row.cells[1].text = f"{quantity:g} {unit}"
            row.cells[2].text = description
            row.cells[3].text = format_idr(unit_price)
            row.cells[4].text = format_idr(line_total)
            for column_index, width in enumerate(column_widths):
                row.cells[column_index].width = Twips(width)
            for column_index, cell in enumerate(row.cells):
                format_cell(
                    cell,
                    align=(WD_ALIGN_PARAGRAPH.CENTER if column_index == 0
                           else WD_ALIGN_PARAGRAPH.RIGHT if column_index in (3, 4)
                           else WD_ALIGN_PARAGRAPH.CENTER if column_index == 1
                           else WD_ALIGN_PARAGRAPH.LEFT),
                    no_wrap=column_index in (1, 3, 4),
                )
        subtotal_row, tax_row, total_row = items_table.rows[-3:]
        set_invoice_row(subtotal_row, "SUBTOTAL PRICE (IDR)", format_idr(invoice[6]))
        if invoice[9]:
            set_invoice_row(tax_row, f"VAT {Decimal(invoice[8] or 0).normalize():f}% (IDR)", format_idr(invoice[9]))
            set_invoice_row(total_row, "TOTAL INCLUDING VAT (IDR)", format_idr(invoice[10]), bold=True)
        else:
            tax_row._tr.getparent().remove(tax_row._tr)
            set_invoice_row(total_row, "TOTAL PRICE (IDR)", format_idr(invoice[10]), bold=True)

    payment_table = tables[2]
    term_ppr = []
    if len(payment_table.rows) > 2:
        for row_index in (1, 2):
            original_paragraph = payment_table.cell(row_index, 2).paragraphs[0]
            term_ppr.append(deepcopy(original_paragraph._p.pPr) if original_paragraph._p.pPr is not None else None)
    if invoice[11]:
        import re
        term_lines = []
        for line in str(invoice[11]).replace("\r", "\n").replace("/", "\n").splitlines():
            clean_line = re.sub(r"^[\s\u2022\u25cf\u25aa\-]+", "", line).strip()
            clean_line = re.sub(r"^\d+[.)]\s+", "", clean_line)
            if clean_line:
                term_lines.append(clean_line)
        payment_table.cell(1, 2).text = term_lines[0] if term_lines else "-"
        if len(payment_table.rows) > 2:
            payment_table.cell(2, 2).text = term_lines[1] if len(term_lines) > 1 else ""
    payment_table.autofit = False
    for column, width in zip(payment_table.columns, (1800, 200, 8525)):
        column.width = Twips(width)
    for row in payment_table.rows:
        for cell, width in zip(row.cells, (1800, 200, 8525)):
            cell.width = Twips(width)
    for row_index, row in enumerate(payment_table.rows):
        for cell_index, cell in enumerate(row.cells):
            is_term_list = cell_index == 2 and row_index in (1, 2) and bool(cell.text.strip())
            format_cell(cell, align=WD_ALIGN_PARAGRAPH.LEFT, preserve_list=is_term_list)
            if is_term_list:
                paragraph = cell.paragraphs[0]
                paragraph.style = document.styles["List Paragraph"]
                original_ppr = term_ppr[row_index - 1] if row_index - 1 < len(term_ppr) else None
                if original_ppr is not None:
                    if paragraph._p.pPr is not None:
                        paragraph._p.remove(paragraph._p.pPr)
                    paragraph._p.insert(0, deepcopy(original_ppr))
    # The amount words row should align with the payment terms row, with the
    # value starting just after the separator instead of drifting right.
    words_cell = payment_table.cell(0, 2)
    for paragraph in words_cell.paragraphs:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        paragraph.paragraph_format.left_indent = Twips(0)
        paragraph.paragraph_format.first_line_indent = Twips(0)
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
    amount = int(Decimal(invoice[10] or 0).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    ones = (
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
        "eighteen", "nineteen",
    )
    tens_words = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")

    def words_under_thousand(value):
        parts = []
        hundreds, value = divmod(value, 100)
        if hundreds:
            parts.extend([ones[hundreds], "hundred"])
        if value >= 20:
            parts.append(tens_words[value // 10])
            if value % 10:
                parts.append(ones[value % 10])
        elif value:
            parts.append(ones[value])
        return " ".join(parts)

    group_names = ("", "thousand", "million", "billion", "trillion")
    words = []
    remaining = amount
    group_index = 0
    while remaining:
        group = remaining % 1000
        if group:
            group_words = words_under_thousand(group)
            if group_index:
                group_words += f" {group_names[group_index]}"
            words.insert(0, group_words)
        remaining //= 1000
        group_index += 1
    amount_words = " ".join(words) or "zero"
    words_cell.text = f"Rupiah, {amount_words[0].upper()}{amount_words[1:]}"

    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            run.font.name = "Aptos"
            run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Aptos")
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        run.font.name = "Aptos"
                        run._element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "Aptos")

    safe_number = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in invoice[1])
    if request.GET.get("format", "").lower() == "pdf":
        buffer = BytesIO()
        document.save(buffer)
        try:
            pdf_bytes = docx_to_pdf(buffer.getvalue(), f"{safe_number}_invoice")
        except PdfConversionError as exc:
            logger.error("PDF invoice %s gagal: %s", invoice[1], exc)
            messages.error(request, "PDF invoice belum berhasil dibuat. Unduh versi DOCX, atau pastikan Microsoft "
                                    "Word (Windows) / LibreOffice (server) terpasang.")
            return redirect("invoice_list")
        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{safe_number}.pdf"'
        return response
    output = BytesIO()
    document.save(output)
    response = HttpResponse(output.getvalue(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    response["Content-Disposition"] = f'attachment; filename="{safe_number}.docx"'
    return response


@business_permission_required("QUOTATION", lambda request, quotation_id, action: "SETUJUI" if action == "kirim" else "UBAH")
def quotation_status(request, quotation_id, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    transitions = {
        "kirim": ("DRAFT", "TERKIRIM"),
        "terima": ("TERKIRIM", "DITERIMA_KLIEN"),
        "tolak": ("TERKIRIM", "DITOLAK_KLIEN"),
    }
    if action not in transitions:
        raise Http404("Aksi quotation tidak ditemukan.")
    old_status, new_status = transitions[action]
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, pic_pengguna_id FROM quotation WHERE id = %s FOR UPDATE",
                    [quotation_id],
                )
                quote = cursor.fetchone()
                if not quote:
                    raise Http404("Quotation tidak ditemukan.")
                if quote[0] != old_status:
                    messages.error(request, "Status quotation sudah berubah; muat ulang daftar.")
                elif new_status == "TERKIRIM":
                    cursor.execute(
                        "UPDATE quotation SET status = %s, dikirim_pada = now(), dikirim_oleh = %s, diubah_pada = now() WHERE id = %s",
                        [new_status, quote[1], quotation_id],
                    )
                    messages.success(request, "Quotation ditandai sudah dikirim ke klien.")
                else:
                    cursor.execute(
                        "UPDATE quotation SET status = %s, diubah_pada = now() WHERE id = %s",
                        [new_status, quotation_id],
                    )
                    messages.success(request, "Jawaban klien pada quotation berhasil dicatat.")
    except DatabaseError:
        messages.error(request, "Status quotation belum berhasil diperbarui.")
    return redirect("quotation_list")


def _current_pengguna_id(cursor, request):
    """Map the Django user to a pengguna row, creating one when it is missing."""
    user_email = (request.user.email or "").strip().lower()
    if not user_email:
        user_email = f"{request.user.get_username()}@local.invalid"
    cursor.execute("SELECT id FROM pengguna WHERE lower(email) = lower(%s)", [user_email])
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute(
        "INSERT INTO pengguna (nama, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
        [request.user.get_full_name() or request.user.get_username(), user_email, make_password(None)],
    )
    return cursor.fetchone()[0]


@business_permission_required("SERAH_TERIMA", "BUAT")
def sale_delivery(request, quotation_id):
    """Record delivery of sold products: creates the sales order, the handover,
    marks the chosen inventory units as TERJUAL and logs stock movement."""
    database_error = None
    quote = None
    items = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT q.id, q.nomor, q.status, q.perusahaan_id, co.kode, q.klien_id,
                       kl.nama_perusahaan, q.total, q.tanggal,
                       EXISTS (SELECT 1 FROM pesanan_penjualan pp WHERE pp.quotation_id = q.id),
                       COALESCE((SELECT kk.nama FROM kontak_klien kk WHERE kk.klien_id = kl.id
                                 ORDER BY kk.utama DESC, kk.id LIMIT 1), '')
                FROM quotation q
                JOIN perusahaan co ON co.id = q.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                WHERE q.id = %s AND q.jenis = 'PENJUALAN'
                """,
                [quotation_id],
            )
            quote = cursor.fetchone()
            if quote:
                cursor.execute(
                    """
                    SELECT qi.id, qi.model_alat_id, qi.deskripsi_item, qi.kuantitas, qi.satuan,
                           qi.harga_satuan, qi.subtotal
                    FROM quotation_item qi WHERE qi.quotation_id = %s ORDER BY qi.id
                    """,
                    [quotation_id],
                )
                for row in cursor.fetchall():
                    units = []
                    if row[1]:
                        cursor.execute(
                            """
                            SELECT u.id, COALESCE(u.nomor_seri, '-'), COALESCE(u.kode_inventaris, ''),
                                   u.kondisi, u.dapat_dijual, COALESCE(l.nama, '')
                            FROM unit_alat u LEFT JOIN lokasi l ON l.id = u.lokasi_id
                            WHERE u.model_alat_id = %s AND u.status = 'TERSEDIA'
                            ORDER BY u.dapat_dijual DESC, u.id
                            """,
                            [row[1]],
                        )
                        units = [
                            {"id": u[0], "serial": u[1], "code": u[2], "condition": u[3],
                             "for_sale": u[4], "location": u[5]}
                            for u in cursor.fetchall()
                        ]
                    qty = Decimal(row[3])
                    items.append({
                        "id": row[0], "model_id": row[1], "description": row[2],
                        "quantity": qty, "unit": row[4], "price": row[5], "subtotal": row[6],
                        "tracked": bool(row[1]),
                        "needed": int(qty) if row[1] and qty == qty.to_integral_value() else None,
                        "units": units,
                    })
    except DatabaseError:
        database_error = "Belum dapat membaca quotation penjualan. Periksa database."

    if not database_error:
        if not quote:
            raise Http404("Quotation penjualan tidak ditemukan.")
        if quote[9]:
            messages.info(request, "Barang untuk quotation ini sudah tercatat terkirim.")
            return redirect("quotation_list")
        if quote[2] != "DITERIMA_KLIEN":
            messages.error(request, "Barang hanya bisa dikirim setelah quotation disetujui klien.")
            return redirect("quotation_list")

    initial = {"received_by": quote[10] if quote else "",
               "delivered_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M")}
    form = SaleDeliveryForm(request.POST or None, initial=initial)
    selected = {}
    if request.method == "POST" and quote:
        for item in items:
            chosen = [int(v) for v in request.POST.getlist(f"units_{item['id']}") if v.isdigit()]
            selected[item["id"]] = chosen
            available = {u["id"] for u in item["units"]}
            if item["tracked"] and any(c not in available for c in chosen):
                form.add_error(None, f"Unit untuk \"{item['description']}\" tidak lagi tersedia. Muat ulang halaman.")
            elif item["tracked"] and item["needed"] is not None and len(chosen) != item["needed"]:
                form.add_error(None, f"Pilih tepat {item['needed']} unit untuk \"{item['description']}\" (dipilih {len(chosen)}).")
        all_chosen = [c for values in selected.values() for c in values]
        if len(all_chosen) != len(set(all_chosen)):
            form.add_error(None, "Satu unit tidak boleh dipilih untuk dua item.")
    if request.method == "POST" and quote and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT status FROM quotation WHERE id = %s FOR UPDATE", [quotation_id])
                    if cursor.fetchone()[0] != "DITERIMA_KLIEN":
                        raise DatabaseError("quotation status changed")
                    cursor.execute("SELECT 1 FROM pesanan_penjualan WHERE quotation_id = %s", [quotation_id])
                    if cursor.fetchone():
                        raise DatabaseError("already delivered")
                    user_id = _current_pengguna_id(cursor, request)
                    order_number = f"SO/{quote[1]}"[:80]
                    cursor.execute(
                        """
                        INSERT INTO pesanan_penjualan
                            (perusahaan_id, nomor_pesanan, quotation_id, klien_id, tanggal_pesanan,
                             status, total, catatan, dibuat_oleh)
                        VALUES (%s, %s, %s, %s, %s, 'DIKIRIM', %s, %s, %s) RETURNING id
                        """,
                        [quote[3], order_number, quotation_id, quote[5], data["delivered_at"].date(),
                         quote[7], data["notes"] or None, user_id],
                    )
                    order_id = cursor.fetchone()[0]
                    cursor.execute(
                        """
                        INSERT INTO serah_terima_penjualan
                            (pesanan_id, tanggal, diserahkan_oleh, diterima_oleh, catatan)
                        VALUES (%s, %s, %s, %s, %s) RETURNING id
                        """,
                        [order_id, data["delivered_at"], user_id, data["received_by"], data["notes"] or None],
                    )
                    handover_id = cursor.fetchone()[0]
                    for item in items:
                        cursor.execute(
                            """
                            INSERT INTO pesanan_penjualan_item
                                (pesanan_id, quotation_item_id, model_alat_id, deskripsi_snapshot,
                                 kuantitas, harga_satuan, subtotal)
                            VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id
                            """,
                            [order_id, item["id"], item["model_id"], item["description"],
                             item["quantity"], item["price"], item["subtotal"]],
                        )
                        order_item_id = cursor.fetchone()[0]
                        chosen = selected.get(item["id"], [])
                        if not chosen:
                            cursor.execute(
                                """
                                INSERT INTO serah_terima_penjualan_item
                                    (serah_terima_id, pesanan_item_id, kuantitas)
                                VALUES (%s, %s, %s)
                                """,
                                [handover_id, order_item_id, item["quantity"]],
                            )
                        for unit_id in chosen:
                            cursor.execute(
                                "SELECT status FROM unit_alat WHERE id = %s FOR UPDATE", [unit_id]
                            )
                            if cursor.fetchone()[0] != "TERSEDIA":
                                raise DatabaseError("unit no longer available")
                            cursor.execute(
                                """
                                INSERT INTO serah_terima_penjualan_item
                                    (serah_terima_id, pesanan_item_id, unit_alat_id, kuantitas)
                                VALUES (%s, %s, %s, 1)
                                """,
                                [handover_id, order_item_id, unit_id],
                            )
                            cursor.execute("UPDATE unit_alat SET status = 'TERJUAL' WHERE id = %s", [unit_id])
                            cursor.execute(
                                """
                                INSERT INTO riwayat_unit
                                    (unit_alat_id, jenis, nilai_lama, nilai_baru,
                                     referensi_tipe, referensi_id, dicatat_oleh, catatan)
                                VALUES (%s, 'STATUS', 'TERSEDIA', 'TERJUAL', 'PENJUALAN', %s, %s, %s)
                                """,
                                [unit_id, order_id, user_id, f"Terjual ke {quote[6]} ({order_number})"],
                            )
                            cursor.execute(
                                """
                                INSERT INTO audit_perubahan
                                    (jenis_entitas, entitas_id, aksi, pengguna_id, alasan,
                                     nilai_sebelum, nilai_sesudah, metadata)
                                VALUES ('unit_alat', %s, 'UBAH_STATUS', %s, %s,
                                        %s::jsonb, %s::jsonb, %s::jsonb)
                                """,
                                [unit_id, user_id, f"Unit dijual melalui {order_number}.",
                                 json.dumps({"status": "TERSEDIA"}), json.dumps({"status": "TERJUAL"}),
                                 json.dumps({"django_user": request.user.get_username()})],
                            )
                        if item["model_id"] and (chosen or item["quantity"]):
                            cursor.execute(
                                """
                                INSERT INTO pergerakan_stok
                                    (perusahaan_id, model_alat_id, jenis, kuantitas,
                                     referensi_tipe, referensi_id, dicatat_oleh, catatan)
                                VALUES (%s, %s, 'KELUAR', %s, 'PENJUALAN', %s, %s, %s)
                                """,
                                [quote[3], item["model_id"], len(chosen) or item["quantity"],
                                 order_id, user_id, f"Penjualan {order_number}"],
                            )
            messages.success(request, f"Serah terima penjualan {order_number} tercatat; unit terpilih ditandai TERJUAL.")
            return redirect("quotation_list")
        except DatabaseError:
            logger.exception("Sale delivery failed for quotation %s", quotation_id)
            form.add_error(None, "Pengiriman belum tersimpan. Status quotation atau unit mungkin sudah berubah; muat ulang halaman.")

    return render(request, "portal/sale_delivery.html", {
        "form": form, "quote": quote, "items": items, "database_error": database_error,
        "selected": selected,
    })


def _sync_unit_accessories(cursor, unit_id, accessories):
    cursor.execute(
        "SELECT id, paket_alat_id FROM paket_unit WHERE unit_utama_id = %s FOR UPDATE",
        [unit_id],
    )
    package_unit = cursor.fetchone()
    if not package_unit and not accessories:
        return

    if package_unit:
        package_unit_id, package_id = package_unit
        cursor.execute("DELETE FROM paket_unit_item WHERE paket_unit_id = %s", [package_unit_id])
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

    quantities_by_name = {}
    has_serial_by_name = {}
    for name, quantity, _serial_number in accessories:
        key = name.casefold()
        quantities_by_name[key] = quantities_by_name.get(key, Decimal("0")) + quantity
        has_serial_by_name[key] = has_serial_by_name.get(key, False) or bool(_serial_number)

    for name, quantity, serial_number in accessories:
        cursor.execute(
            """
            SELECT id FROM paket_alat_item
            WHERE paket_alat_id = %s AND lower(nama_item) = lower(%s)
            ORDER BY id LIMIT 1
            """,
            [package_id, name],
        )
        package_item = cursor.fetchone()
        if package_item:
            package_item_id = package_item[0]
            cursor.execute(
                "UPDATE paket_alat_item SET jumlah_standar = %s, punya_serial = %s WHERE id = %s",
                [quantities_by_name[name.casefold()], has_serial_by_name[name.casefold()], package_item_id],
            )
        else:
            cursor.execute(
                """
                INSERT INTO paket_alat_item
                    (paket_alat_id, nama_item, jumlah_standar, satuan, punya_serial)
                VALUES (%s, %s, %s, 'unit', %s) RETURNING id
                """,
                [package_id, name, quantities_by_name[name.casefold()], has_serial_by_name[name.casefold()]],
            )
            package_item_id = cursor.fetchone()[0]
        cursor.execute(
            """
            INSERT INTO paket_unit_item
                (paket_unit_id, paket_alat_item_id, jumlah, kondisi, nomor_seri)
            VALUES (%s, %s, %s, 'BAIK', %s)
            """,
            [package_unit_id, package_item_id, quantity, serial_number or None],
        )


_DASHBOARD_PERIODS = {"7": 7, "30": 30, "90": 90, "365": 365}
_MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _rupiah_short(value):
    value = Decimal(value or 0)
    sign = "-" if value < 0 else ""
    value = abs(value)
    for limit, suffix in ((Decimal("1000000000000"), " T"), (Decimal("1000000000"), " M"),
                          (Decimal("1000000"), " jt"), (Decimal("1000"), " rb")):
        if value >= limit:
            number = (value / limit).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            text = f"{number:,.1f}".replace(",", "#").replace(".", ",").replace("#", ".")
            if text.endswith(",0"):
                text = text[:-2]
            return f"{sign}Rp {text}{suffix}"
    return f"{sign}Rp {int(value):,}".replace(",", ".")


def _dashboard_query(cursor, sql, params=None):
    """Jalankan satu kueri dashboard; kembalikan [] bila tabel/kolom belum tersedia."""
    try:
        with transaction.atomic():
            cursor.execute(sql, params or [])
            return cursor.fetchall()
    except DatabaseError:
        logger.warning("Kueri dashboard gagal", exc_info=True)
        return None


@business_permission_required("SEMUA_DATA", "LIHAT")
def dashboard(request):
    period_key = request.GET.get("periode", "30")
    if period_key not in _DASHBOARD_PERIODS:
        period_key = "30"
    period_days = _DASHBOARD_PERIODS[period_key]
    today = date.today()
    period_start = today - timedelta(days=period_days - 1)
    previous_start = period_start - timedelta(days=period_days)

    summary = {
        "available_units": 0, "rented_units": 0, "maintenance_units": 0, "total_units": 0,
        "utilization": 0, "active_rentals": 0, "rentals_period": 0, "rentals_previous": 0,
        "quotation_open": 0, "quotation_open_value": "Rp 0", "quotation_period": 0,
        "quotation_won": 0, "win_rate": None,
        "revenue_period": "Rp 0", "revenue_previous": "Rp 0", "revenue_change": None,
        "receivable": "Rp 0", "receivable_count": 0, "overdue_invoices": 0,
        "active_projects": 0, "planned_projects": 0, "done_projects_period": 0,
        "clients": 0,
    }
    companies, roles, alerts, activities = [], [], [], []
    running_rentals, recent_quotations, map_points = [], [], []
    revenue_chart, company_breakdown, category_usage = [], [], []
    database_error = None

    try:
        with connection.cursor() as cursor:
            rows = _dashboard_query(cursor, """
                SELECT COUNT(*) FILTER (WHERE status = 'TERSEDIA'),
                       COUNT(*) FILTER (WHERE status = 'DISEWA'),
                       COUNT(*) FILTER (WHERE status = 'PERAWATAN'),
                       COUNT(*)
                FROM unit_alat WHERE status NOT IN ('DIARSIPKAN', 'TERJUAL')
            """)
            if rows is None:
                raise DatabaseError("unit_alat")
            available, rented, maintenance, total = rows[0]
            summary.update(available_units=available, rented_units=rented,
                           maintenance_units=maintenance, total_units=total,
                           utilization=round(rented * 100 / total) if total else 0)

            rows = _dashboard_query(cursor, """
                SELECT COUNT(*) FILTER (WHERE status = 'BERJALAN'),
                       COUNT(*) FILTER (WHERE dibuat_pada::date >= %s),
                       COUNT(*) FILTER (WHERE dibuat_pada::date >= %s AND dibuat_pada::date < %s)
                FROM rental
            """, [period_start, previous_start, period_start])
            if rows:
                summary.update(active_rentals=rows[0][0], rentals_period=rows[0][1],
                               rentals_previous=rows[0][2])

            rows = _dashboard_query(cursor, """
                SELECT COUNT(*) FILTER (WHERE status IN ('DRAFT','DIAJUKAN','DISETUJUI','TERKIRIM')),
                       COALESCE(SUM(total) FILTER (WHERE status IN ('DIAJUKAN','DISETUJUI','TERKIRIM')), 0),
                       COUNT(*) FILTER (WHERE tanggal >= %s),
                       COUNT(*) FILTER (WHERE tanggal >= %s AND status = 'DITERIMA_KLIEN'),
                       COUNT(*) FILTER (WHERE tanggal >= %s
                                        AND status IN ('DITERIMA_KLIEN','DITOLAK_KLIEN','KEDALUWARSA'))
                FROM quotation
            """, [period_start, period_start, period_start])
            if rows:
                open_count, open_value, q_period, q_won, q_closed = rows[0]
                summary.update(quotation_open=open_count, quotation_open_value=_rupiah_short(open_value),
                               quotation_period=q_period, quotation_won=q_won,
                               win_rate=round(q_won * 100 / q_closed) if q_closed else None)

            rows = _dashboard_query(cursor, """
                SELECT COALESCE(SUM(total) FILTER (WHERE status = 'DIBAYAR'
                                AND COALESCE(dibayar_pada::date, tanggal) >= %s), 0),
                       COALESCE(SUM(total) FILTER (WHERE status = 'DIBAYAR'
                                AND COALESCE(dibayar_pada::date, tanggal) >= %s
                                AND COALESCE(dibayar_pada::date, tanggal) < %s), 0),
                       COALESCE(SUM(total) FILTER (WHERE status = 'DITERBITKAN'), 0),
                       COUNT(*) FILTER (WHERE status = 'DITERBITKAN'),
                       COUNT(*) FILTER (WHERE status = 'DITERBITKAN' AND jatuh_tempo < %s)
                FROM invoice
            """, [period_start, previous_start, period_start, today])
            if rows:
                paid, paid_prev, receivable, receivable_count, overdue = rows[0]
                summary.update(revenue_period=_rupiah_short(paid), revenue_previous=_rupiah_short(paid_prev),
                               receivable=_rupiah_short(receivable), receivable_count=receivable_count,
                               overdue_invoices=overdue,
                               revenue_change=(round((paid - paid_prev) * 100 / paid_prev)
                                               if paid_prev else None))

            rows = _dashboard_query(cursor, """
                SELECT COUNT(*) FILTER (WHERE status = 'BERJALAN'),
                       COUNT(*) FILTER (WHERE status = 'DIRENCANAKAN'),
                       COUNT(*) FILTER (WHERE status = 'SELESAI'
                                        AND COALESCE(tanggal_selesai_aktual, tanggal_selesai_rencana) >= %s)
                FROM proyek
            """, [period_start])
            if rows:
                summary.update(active_projects=rows[0][0], planned_projects=rows[0][1],
                               done_projects_period=rows[0][2])

            rows = _dashboard_query(cursor, "SELECT COUNT(*) FROM klien WHERE aktif")
            if rows:
                summary["clients"] = rows[0][0]

            # Tren pendapatan (invoice dibayar) per bulan
            months = 12 if period_days >= 365 else 6
            first_month = date(today.year, today.month, 1)
            for _ in range(months - 1):
                first_month = (first_month - timedelta(days=1)).replace(day=1)
            rows = _dashboard_query(cursor, """
                SELECT date_trunc('month', COALESCE(dibayar_pada::date, tanggal))::date AS bulan,
                       COALESCE(SUM(total) FILTER (WHERE status = 'DIBAYAR'), 0),
                       COALESCE(SUM(total) FILTER (WHERE status = 'DITERBITKAN'), 0)
                FROM invoice
                WHERE status IN ('DIBAYAR','DITERBITKAN')
                  AND COALESCE(dibayar_pada::date, tanggal) >= %s
                GROUP BY 1
            """, [first_month]) or []
            by_month = {row[0]: (row[1], row[2]) for row in rows}
            cursor_month = first_month
            for _ in range(months):
                paid, issued = by_month.get(cursor_month, (0, 0))
                revenue_chart.append({"label": f"{_MONTH_ABBR[cursor_month.month - 1]} {str(cursor_month.year)[2:]}",
                                      "paid": float(paid), "issued": float(issued),
                                      "paid_text": _rupiah_short(paid), "issued_text": _rupiah_short(issued)})
                cursor_month = (cursor_month + timedelta(days=32)).replace(day=1)

            # Ringkasan per perusahaan
            rows = _dashboard_query(cursor, """
                SELECT p.kode, p.nama_resmi,
                       (SELECT COUNT(*) FROM rental r WHERE r.perusahaan_id = p.id AND r.status = 'BERJALAN'),
                       (SELECT COUNT(*) FROM quotation q WHERE q.perusahaan_id = p.id AND q.tanggal >= %s),
                       (SELECT COALESCE(SUM(i.total), 0) FROM invoice i WHERE i.perusahaan_id = p.id
                          AND i.status = 'DIBAYAR' AND COALESCE(i.dibayar_pada::date, i.tanggal) >= %s),
                       (SELECT COUNT(*) FROM proyek pr WHERE pr.perusahaan_id = p.id AND pr.status = 'BERJALAN')
                FROM perusahaan p WHERE p.aktif ORDER BY p.kode
            """, [period_start, period_start]) or []
            company_breakdown = [
                {"code": r[0], "name": r[1], "rentals": r[2], "quotations": r[3],
                 "revenue": _rupiah_short(r[4]), "revenue_raw": float(r[4]), "projects": r[5]}
                for r in rows
            ]
            companies = [{"code": c["code"], "name": c["name"]} for c in company_breakdown]

            # Pemakaian per kategori alat
            rows = _dashboard_query(cursor, """
                SELECT COALESCE(k.nama, 'Lainnya'),
                       COUNT(*) FILTER (WHERE u.status = 'DISEWA'), COUNT(*)
                FROM unit_alat u
                JOIN model_alat m ON m.id = u.model_alat_id
                LEFT JOIN kategori_alat k ON k.id = m.kategori_alat_id
                WHERE u.status NOT IN ('DIARSIPKAN', 'TERJUAL')
                GROUP BY 1 ORDER BY 3 DESC, 1 LIMIT 6
            """) or []
            category_usage = [
                {"name": r[0], "rented": r[1], "total": r[2],
                 "percent": round(r[1] * 100 / r[2]) if r[2] else 0}
                for r in rows
            ]

            # Rental berjalan
            rows = _dashboard_query(cursor, """
                SELECT r.id, r.nomor_rental, kl.nama_perusahaan, p.kode,
                       COALESCE(r.tanggal_mulai_aktual, r.tanggal_mulai_rencana),
                       r.tanggal_selesai_rencana, r.lokasi_penggunaan,
                       (SELECT COUNT(*) FROM rental_item ri WHERE ri.rental_id = r.id)
                FROM rental r
                JOIN klien kl ON kl.id = r.klien_id
                JOIN perusahaan p ON p.id = r.perusahaan_id
                WHERE r.status = 'BERJALAN'
                ORDER BY r.tanggal_selesai_rencana NULLS LAST, r.id DESC
                LIMIT 6
            """) or []
            for r in rows:
                start, end = r[4], r[5]
                progress, days_left = None, None
                if start and end and end >= start:
                    span = max((end - start).days, 1)
                    progress = max(0, min(100, round((today - start).days * 100 / span)))
                    days_left = (end - today).days
                running_rentals.append({"id": r[0], "number": r[1], "client": r[2], "company": r[3],
                                        "start": start, "end": end, "location": r[6], "units": r[7],
                                        "progress": progress, "days_left": days_left,
                                        "days_late": -days_left if days_left is not None and days_left < 0 else 0})

            # Quotation terbaru
            rows = _dashboard_query(cursor, """
                SELECT q.id, q.nomor, kl.nama_perusahaan, p.kode, q.jenis, q.status, q.total, q.tanggal
                FROM quotation q
                JOIN klien kl ON kl.id = q.klien_id
                JOIN perusahaan p ON p.id = q.perusahaan_id
                ORDER BY q.tanggal DESC, q.id DESC LIMIT 6
            """) or []
            recent_quotations = [
                {"id": r[0], "number": r[1], "client": r[2], "company": r[3],
                 "type": (r[4] or "").replace("_", " ").title(), "status": r[5],
                 "status_label": (r[5] or "").replace("_", " ").title(),
                 "total": _rupiah_short(r[6]), "date": r[7]}
                for r in rows
            ]

            # Titik lokasi peta (rental berjalan & proyek aktif)
            rows = _dashboard_query(cursor, """
                SELECT 'rental', r.nomor_rental, kl.nama_perusahaan, r.lokasi_penggunaan,
                       r.latitude, r.longitude
                FROM rental r JOIN klien kl ON kl.id = r.klien_id
                WHERE r.status = 'BERJALAN' AND r.latitude IS NOT NULL AND r.longitude IS NOT NULL
                UNION ALL
                SELECT 'proyek', pr.kode_proyek, pr.nama, pr.lokasi_teks, pr.latitude, pr.longitude
                FROM proyek pr
                WHERE pr.status IN ('BERJALAN','DIRENCANAKAN')
                  AND pr.latitude IS NOT NULL AND pr.longitude IS NOT NULL
                LIMIT 200
            """) or []
            map_points = [{"type": r[0], "code": r[1], "name": r[2], "location": r[3] or "",
                           "lat": float(r[4]), "lng": float(r[5])} for r in rows]

            # Peringatan & tindakan
            for r in _dashboard_query(cursor, """
                SELECT r.nomor_rental, kl.nama_perusahaan, r.tanggal_selesai_rencana
                FROM rental r JOIN klien kl ON kl.id = r.klien_id
                WHERE r.status = 'BERJALAN' AND r.tanggal_selesai_rencana <= %s
                ORDER BY r.tanggal_selesai_rencana LIMIT 5
            """, [today + timedelta(days=7)]) or []:
                late = r[2] < today
                alerts.append({
                    "level": "danger" if late else "warning", "icon": "rental",
                    "title": ("Rental melewati jadwal kembali" if late else "Rental segera berakhir"),
                    "body": f"{r[0]} · {r[1]}",
                    "meta": (f"Terlambat {(today - r[2]).days} hari" if late
                             else ("Berakhir hari ini" if r[2] == today else f"Berakhir {(r[2] - today).days} hari lagi")),
                    "action": "Koordinasikan pengembalian atau perpanjangan",
                    "url": "rental_handover"})
            for r in _dashboard_query(cursor, """
                SELECT i.nomor, kl.nama_perusahaan, i.jatuh_tempo, i.total
                FROM invoice i JOIN quotation q ON q.id = i.quotation_id
                JOIN klien kl ON kl.id = q.klien_id
                WHERE i.status = 'DITERBITKAN' AND i.jatuh_tempo IS NOT NULL AND i.jatuh_tempo <= %s
                ORDER BY i.jatuh_tempo LIMIT 5
            """, [today + timedelta(days=7)]) or []:
                late = r[2] < today
                alerts.append({
                    "level": "danger" if late else "warning", "icon": "invoice",
                    "title": "Invoice jatuh tempo" if late else "Invoice mendekati jatuh tempo",
                    "body": f"{r[0]} · {r[1]} · {_rupiah_short(r[3])}",
                    "meta": (f"Lewat {(today - r[2]).days} hari" if late
                             else f"Jatuh tempo {r[2].strftime('%d/%m/%Y')}"),
                    "action": "Tindak lanjuti pembayaran klien", "url": "invoice_list"})
            for r in _dashboard_query(cursor, """
                SELECT q.nomor, kl.nama_perusahaan, q.status, q.berlaku_sampai
                FROM quotation q JOIN klien kl ON kl.id = q.klien_id
                WHERE q.status = 'DIAJUKAN'
                   OR (q.status = 'TERKIRIM' AND q.berlaku_sampai BETWEEN %s AND %s)
                ORDER BY q.berlaku_sampai NULLS LAST LIMIT 5
            """, [today, today + timedelta(days=5)]) or []:
                waiting = r[2] == "DIAJUKAN"
                alerts.append({
                    "level": "info", "icon": "quotation",
                    "title": "Quotation menunggu persetujuan" if waiting else "Quotation segera kedaluwarsa",
                    "body": f"{r[0]} · {r[1]}",
                    "meta": ("Perlu review internal" if waiting
                             else f"Berlaku s.d. {r[3].strftime('%d/%m/%Y')}"),
                    "action": "Review dan setujui" if waiting else "Follow-up ke klien",
                    "url": "quotation_list"})
            for r in _dashboard_query(cursor, """
                SELECT concat_ws(' ', m.merek, m.nama_model), u.nomor_seri, pw.kalibrasi_berlaku_sampai
                FROM perawatan pw
                JOIN unit_alat u ON u.id = pw.unit_alat_id AND u.status NOT IN ('DIARSIPKAN', 'TERJUAL')
                JOIN model_alat m ON m.id = u.model_alat_id
                WHERE pw.kalibrasi_berlaku_sampai IS NOT NULL
                  AND pw.kalibrasi_berlaku_sampai <= %s
                  AND NOT EXISTS (SELECT 1 FROM perawatan p2 WHERE p2.unit_alat_id = pw.unit_alat_id
                                  AND p2.kalibrasi_berlaku_sampai > pw.kalibrasi_berlaku_sampai)
                ORDER BY pw.kalibrasi_berlaku_sampai LIMIT 4
            """, [today + timedelta(days=30)]) or []:
                late = r[2] < today
                alerts.append({
                    "level": "danger" if late else "warning", "icon": "tool",
                    "title": "Kalibrasi kedaluwarsa" if late else "Kalibrasi segera berakhir",
                    "body": f"{r[0]}" + (f" · S/N {r[1]}" if r[1] else ""),
                    "meta": f"Berlaku s.d. {r[2].strftime('%d/%m/%Y')}",
                    "action": "Jadwalkan kalibrasi ulang", "url": "inventory"})
            if maintenance:
                alerts.append({
                    "level": "info", "icon": "tool", "title": "Unit dalam perawatan",
                    "body": f"{maintenance} unit belum dapat disewakan",
                    "meta": "Status PERAWATAN", "action": "Pantau penyelesaian perawatan",
                    "url": "inventory"})
            order = {"danger": 0, "warning": 1, "info": 2}
            alerts.sort(key=lambda a: order[a["level"]])

            # Aktivitas terbaru dari beberapa sumber
            feed = []
            for r in _dashboard_query(cursor, """
                SELECT r.dibuat_pada, r.nomor_rental, kl.nama_perusahaan, pg.nama
                FROM rental r JOIN klien kl ON kl.id = r.klien_id
                LEFT JOIN pengguna pg ON pg.id = r.dibuat_oleh
                ORDER BY r.dibuat_pada DESC LIMIT 6
            """) or []:
                feed.append({"when": r[0], "kind": "rental", "title": f"Rental {r[1]} dibuat",
                             "detail": r[2], "by": r[3]})
            for r in _dashboard_query(cursor, """
                SELECT q.dibuat_pada, q.nomor, kl.nama_perusahaan, pg.nama, q.status
                FROM quotation q JOIN klien kl ON kl.id = q.klien_id
                LEFT JOIN pengguna pg ON pg.id = q.dibuat_oleh
                ORDER BY q.dibuat_pada DESC LIMIT 6
            """) or []:
                feed.append({"when": r[0], "kind": "quotation", "title": f"Quotation {r[1]} dibuat",
                             "detail": f"{r[2]} · {r[4].replace('_', ' ').title()}", "by": r[3]})
            for r in _dashboard_query(cursor, """
                SELECT COALESCE(i.dibayar_pada, i.diterbitkan_pada, i.dibuat_pada), i.nomor, i.status,
                       i.total, pg.nama
                FROM invoice i LEFT JOIN pengguna pg ON pg.id = i.dibuat_oleh
                ORDER BY 1 DESC LIMIT 6
            """) or []:
                verb = {"DIBAYAR": "dibayar", "DITERBITKAN": "diterbitkan",
                        "DIBATALKAN": "dibatalkan"}.get(r[2], "dibuat")
                feed.append({"when": r[0], "kind": "invoice", "title": f"Invoice {r[1]} {verb}",
                             "detail": _rupiah_short(r[3]), "by": r[4]})
            for r in _dashboard_query(cursor, """
                SELECT ru.waktu, concat_ws(' ', m.merek, m.nama_model), u.nomor_seri,
                       ru.jenis, ru.nilai_lama, ru.nilai_baru, pg.nama
                FROM riwayat_unit ru
                JOIN unit_alat u ON u.id = ru.unit_alat_id
                JOIN model_alat m ON m.id = u.model_alat_id
                LEFT JOIN pengguna pg ON pg.id = ru.dicatat_oleh
                ORDER BY ru.waktu DESC LIMIT 6
            """) or []:
                label = "Status" if r[3] == "STATUS" else "Lokasi"
                feed.append({"when": r[0], "kind": "unit",
                             "title": f"{label} {r[1]} diperbarui",
                             "detail": f"{r[4] or '-'} → {r[5] or '-'}" + (f" · S/N {r[2]}" if r[2] else ""),
                             "by": r[6]})
            feed = [f for f in feed if f["when"]]
            feed.sort(key=lambda f: f["when"], reverse=True)
            activities = feed[:7]

            rows = _dashboard_query(cursor, """
                SELECT kode, nama FROM peran
                ORDER BY CASE kode WHEN 'DIREKTUR_UTAMA' THEN 1 WHEN 'MANAJER' THEN 2
                    WHEN 'BUSINESS_DEVELOPMENT' THEN 3 WHEN 'BUSINESS_SUPPORT' THEN 4
                    WHEN 'TECHNICAL_SALES' THEN 5 ELSE 6 END, nama
            """) or []
            roles = [{"code": r[0], "name": r[1]} for r in rows]
    except DatabaseError:
        logger.exception("Dashboard gagal membaca database")
        database_error = "Belum dapat membaca database. Periksa koneksi PostgreSQL dan file .env."

    user_role = None
    if request.user.is_superuser:
        user_role = "Super Admin"
    else:
        try:
            with connection.cursor() as cursor:
                cursor.execute("""
                    SELECT string_agg(p.nama, ', ' ORDER BY p.nama)
                    FROM pengguna u JOIN pengguna_peran up ON up.pengguna_id = u.id
                    JOIN peran p ON p.id = up.peran_id
                    WHERE lower(u.email) = lower(%s)
                """, [request.user.email])
                user_role = cursor.fetchone()[0]
        except DatabaseError:
            user_role = None

    rental_change = None
    if summary["rentals_previous"]:
        rental_change = round((summary["rentals_period"] - summary["rentals_previous"]) * 100
                              / summary["rentals_previous"])

    hour = timezone_now_hour()
    greeting = ("Selamat pagi" if hour < 11 else "Selamat siang" if hour < 15
                else "Selamat sore" if hour < 18 else "Selamat malam")

    return render(request, "portal/dashboard.html", {
        "companies": companies, "roles": roles, "summary": summary,
        "database_error": database_error, "alerts": alerts, "activities": activities,
        "running_rentals": running_rentals, "recent_quotations": recent_quotations,
        "company_breakdown": company_breakdown, "category_usage": category_usage,
        "revenue_chart": revenue_chart, "map_points": map_points,
        "period_key": period_key, "period_days": period_days,
        "period_options": [("7", "7 hari"), ("30", "30 hari"), ("90", "3 bulan"), ("365", "1 tahun")],
        "period_start": period_start, "today": today, "rental_change": rental_change,
        "user_role": user_role, "greeting": greeting,
        "danger_alerts": sum(1 for a in alerts if a["level"] == "danger"),
    })


def timezone_now_hour():
    from django.utils import timezone
    return timezone.localtime().hour


@business_permission_required("INVENTARIS", "LIHAT")
def inventory(request):
    search = request.GET.get("q", "").strip()[:100]
    category = request.GET.get("kategori", "").strip()[:120]
    include_archived = request.GET.get("arsip") == "1"
    try:
        page_number = max(1, int(request.GET.get("page", "1")))
    except (TypeError, ValueError):
        page_number = 1
    page_size = 50
    total_units = 0
    units = []
    database_error = None

    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*)
                FROM unit_alat AS u
                JOIN model_alat AS m ON m.id = u.model_alat_id
                JOIN kategori_alat AS k ON k.id = m.kategori_alat_id
                LEFT JOIN lokasi AS l ON l.id = u.lokasi_id
                WHERE (%s = TRUE OR u.status NOT IN ('DIARSIPKAN', 'TERJUAL'))
                  AND (%s = '' OR k.nama = %s)
                  AND (%s = '' OR
                       concat_ws(' ', k.nama, m.merek, m.nama_model,
                                 u.nomor_seri, u.om_sn, u.kode_inventaris,
                                 l.nama) ILIKE %s
                       OR EXISTS (
                           SELECT 1 FROM paket_unit pu
                           JOIN paket_unit_item pui ON pui.paket_unit_id = pu.id
                           WHERE pu.unit_utama_id = u.id AND pui.nomor_seri ILIKE %s
                       ))
                """,
                [include_archived, category, category, search, f"%{search}%", f"%{search}%"],
            )
            total_units = cursor.fetchone()[0]
            page_count = max(1, (total_units + page_size - 1) // page_size)
            page_number = min(page_number, page_count)
            cursor.execute(
                """
                SELECT u.id, k.nama, m.merek, m.nama_model,
                       u.nomor_seri, u.om_sn, u.kode_inventaris, u.kondisi,
                       u.status, l.nama, u.foto_url,
                       COALESCE(rh.jumlah_keluar, 0), rh.klien,
                       COALESCE(aksesori.daftar_aksesori, '')
                FROM unit_alat AS u
                JOIN model_alat AS m ON m.id = u.model_alat_id
                JOIN kategori_alat AS k ON k.id = m.kategori_alat_id
                LEFT JOIN lokasi AS l ON l.id = u.lokasi_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(DISTINCT st.id) AS jumlah_keluar,
                           string_agg(DISTINCT kl.nama_perusahaan, ', '
                                      ORDER BY kl.nama_perusahaan) AS klien
                    FROM serah_terima_rental_item AS stri
                    JOIN rental_item AS ri ON ri.id = stri.rental_item_id
                    JOIN serah_terima_rental AS st
                      ON st.id = stri.serah_terima_id AND st.jenis = 'KELUAR'
                    JOIN rental AS r ON r.id = st.rental_id AND r.id = ri.rental_id
                    JOIN klien AS kl ON kl.id = r.klien_id
                    WHERE COALESCE(stri.unit_alat_id, ri.unit_alat_id) = u.id
                ) AS rh ON TRUE
                LEFT JOIN LATERAL (
                    SELECT string_agg(
                               trim_scale(pui.jumlah)::text || 'x ' || pai.nama_item ||
                               CASE WHEN NULLIF(pui.nomor_seri, '') IS NOT NULL
                                    THEN ' (S/N: ' || pui.nomor_seri || ')' ELSE '' END,
                               chr(10) ORDER BY pai.nama_item, pui.nomor_seri
                           ) AS daftar_aksesori
                    FROM paket_unit pu
                    JOIN paket_unit_item pui ON pui.paket_unit_id = pu.id
                    JOIN paket_alat_item pai ON pai.id = pui.paket_alat_item_id
                    WHERE pu.unit_utama_id = u.id
                ) AS aksesori ON TRUE
                WHERE (%s = TRUE OR u.status NOT IN ('DIARSIPKAN', 'TERJUAL'))
                  AND (%s = '' OR k.nama = %s)
                  AND (%s = '' OR
                       concat_ws(' ', k.nama, m.merek, m.nama_model,
                                 u.nomor_seri, u.om_sn, u.kode_inventaris,
                                 l.nama) ILIKE %s
                       OR EXISTS (
                           SELECT 1 FROM paket_unit pu
                           JOIN paket_unit_item pui ON pui.paket_unit_id = pu.id
                           WHERE pu.unit_utama_id = u.id AND pui.nomor_seri ILIKE %s
                       ))
                ORDER BY k.nama, m.merek, m.nama_model, u.nomor_seri
                LIMIT %s OFFSET %s
                """,
                [include_archived, category, category, search, f"%{search}%", f"%{search}%", page_size,
                 (page_number - 1) * page_size],
            )
            def inventory_display_name(brand, model):
                brand = (brand or "").strip()
                model = (model or "").strip()
                if not brand:
                    return model
                # Some inventory records already include the manufacturer in
                # the model/name field. Avoid repeating it in the bold title.
                brand_tokens = [token.casefold() for token in brand.split() if len(token) > 2]
                if any(token in model.casefold() for token in brand_tokens):
                    return model
                return f"{brand} {model}".strip()

            units = [
                {
                    "id": row[0],
                    "category": row[1],
                    "brand": row[2],
                    "model": row[3],
                    "display_name": inventory_display_name(row[2], row[3]),
                    "serial": row[4],
                    "om_sn": row[5],
                    "inventory_code": row[6],
                    "condition": row[7],
                    "status": row[8],
                    "location": row[9],
                    "photo_url": default_storage.url(row[10]) if row[10] else None,
                    "exit_count": row[11],
                    "clients": row[12],
                    "accessories": [line for line in (row[13] or "").splitlines() if line.strip()],
                }
                for row in cursor.fetchall()
            ]
            cursor.execute(
                "SELECT DISTINCT nama FROM kategori_alat "
                "WHERE lower(nama) <> lower(%s) ORDER BY nama",
                ["GNSS Positioning"],
            )
            categories = [row[0] for row in cursor.fetchall()]
    except DatabaseError:
        page_count = 1
        database_error = (
            "Belum dapat membaca inventaris. Periksa apakah skema database sudah dimuat."
        )

    return render(
        request,
        "portal/inventory.html",
        {"units": units, "search": search, "category": category,
         "categories": categories if not database_error else [],
         "database_error": database_error,
         "include_archived": include_archived, "page_number": page_number,
         "page_count": page_count, "total_units": total_units,
         "first_result": (page_number - 1) * page_size + 1 if total_units else 0,
         "last_result": min(page_number * page_size, total_units),
         "has_previous": page_number > 1, "has_next": page_number < page_count,
         "previous_page": page_number - 1, "next_page": page_number + 1},
    )


@business_permission_required("INVENTARIS", "UBAH")
def archive_unit(request, unit_id, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if action not in {"arsipkan", "pulihkan"}:
        raise Http404("Aksi tidak ditemukan.")
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT status FROM unit_alat WHERE id = %s FOR UPDATE", [unit_id])
                row = cursor.fetchone()
                if not row:
                    raise Http404("Unit alat tidak ditemukan.")
                old_status = row[0]
                new_status = old_status
                if action == "arsipkan":
                    if old_status != "TERSEDIA":
                        messages.error(request, "Arsip hanya bisa dilakukan untuk unit berstatus TERSEDIA.")
                    else:
                        new_status = "DIARSIPKAN"
                elif old_status != "DIARSIPKAN":
                    messages.error(request, "Unit ini tidak sedang diarsipkan.")
                    new_status = old_status
                else:
                    cursor.execute(
                        """
                        SELECT COALESCE(
                            (SELECT nilai_sebelum->>'status'
                             FROM audit_perubahan
                             WHERE jenis_entitas = 'unit_alat' AND entitas_id = %s
                               AND aksi = 'UBAH_STATUS'
                               AND nilai_sesudah->>'status' = 'DIARSIPKAN'
                             ORDER BY waktu DESC LIMIT 1),
                            'TERSEDIA'
                        )
                        """,
                        [unit_id],
                    )
                    new_status = cursor.fetchone()[0]

                if new_status != old_status:
                    cursor.execute("UPDATE unit_alat SET status = %s WHERE id = %s", [new_status, unit_id])
                    cursor.execute(
                        """
                        INSERT INTO audit_perubahan
                            (jenis_entitas, entitas_id, aksi, alasan,
                             nilai_sebelum, nilai_sesudah, metadata)
                        VALUES ('unit_alat', %s, 'UBAH_STATUS', %s,
                                %s::jsonb, %s::jsonb, %s::jsonb)
                        """,
                        [unit_id, "Arsip inventaris" if action == "arsipkan" else "Pemulihan inventaris",
                         json.dumps({"status": old_status}), json.dumps({"status": new_status}),
                         json.dumps({"django_user": request.user.get_username()})],
                    )
                    messages.success(request, "Unit berhasil diarsipkan." if action == "arsipkan" else "Unit berhasil dipulihkan.")
    except DatabaseError:
        messages.error(request, "Perubahan arsip belum tersimpan. Periksa koneksi database.")
    return redirect("inventory")


@business_permission_required("INVENTARIS", "LIHAT")
def inventory_history(request, unit_id):
    unit = None
    exits = []
    database_error = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, k.nama, m.merek, m.nama_model, u.nomor_seri
                FROM unit_alat AS u
                JOIN model_alat AS m ON m.id = u.model_alat_id
                JOIN kategori_alat AS k ON k.id = m.kategori_alat_id
                WHERE u.id = %s
                """,
                [unit_id],
            )
            row = cursor.fetchone()
            if row:
                unit = {
                    "id": row[0], "category": row[1], "brand": row[2],
                    "model": row[3], "serial": row[4],
                }
                cursor.execute(
                    """
                    SELECT DISTINCT st.id, st.tanggal, r.nomor_rental,
                           kl.nama_perusahaan, st.penerima_nama
                    FROM serah_terima_rental_item AS stri
                    JOIN rental_item AS ri ON ri.id = stri.rental_item_id
                    JOIN serah_terima_rental AS st
                      ON st.id = stri.serah_terima_id AND st.jenis = 'KELUAR'
                    JOIN rental AS r ON r.id = st.rental_id AND r.id = ri.rental_id
                    JOIN klien AS kl ON kl.id = r.klien_id
                    WHERE COALESCE(stri.unit_alat_id, ri.unit_alat_id) = %s
                    ORDER BY st.tanggal DESC, st.id DESC
                    """,
                    [unit_id],
                )
                exits = [
                    {"date": item[1], "rental_number": item[2],
                     "client": item[3], "recipient": item[4]}
                    for item in cursor.fetchall()
                ]
    except DatabaseError:
        database_error = "Belum dapat membaca riwayat. Periksa skema database."

    if unit is None and not database_error:
        raise Http404("Unit alat tidak ditemukan.")
    return render(
        request,
        "portal/inventory_history.html",
        {"unit": unit, "exits": exits, "database_error": database_error},
    )


@business_permission_required("RENTAL", "LIHAT")
def rental_list(request):
    search = request.GET.get("q", "").strip()[:100]
    status_filter = request.GET.get("status", "").strip()
    allowed_statuses = {"BERJALAN", "SELESAI", "DIBATALKAN", "DIPROSES"}
    if status_filter not in allowed_statuses:
        status_filter = ""
    rentals = []
    database_error = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id, p.kode, r.nomor_rental, kl.nama_perusahaan,
                       r.tanggal_mulai_rencana, r.tanggal_selesai_rencana,
                       r.status, COALESCE(items.jumlah_unit, 0), items.daftar_unit,
                       q.id, q.nomor, inv.id, inv.nomor,
                       r.lokasi_penggunaan, r.latitude, r.longitude
                FROM rental r
                JOIN perusahaan p ON p.id = r.perusahaan_id
                JOIN klien kl ON kl.id = r.klien_id
                LEFT JOIN quotation q ON q.id = r.quotation_id
                LEFT JOIN invoice inv ON inv.id = r.invoice_id
                LEFT JOIN LATERAL (
                    SELECT COUNT(ri.id) AS jumlah_unit,
                           string_agg(
                               DISTINCT concat_ws(' ', m.merek, m.nama_model,
                                   CASE WHEN u.nomor_seri IS NOT NULL
                                        THEN 'S/N ' || u.nomor_seri END,
                                   '(' || u.status || ')'),
                               ', ' ORDER BY concat_ws(' ', m.merek, m.nama_model,
                                   CASE WHEN u.nomor_seri IS NOT NULL
                                        THEN 'S/N ' || u.nomor_seri END,
                                   '(' || u.status || ')')
                           ) AS daftar_unit
                    FROM rental_item ri
                    LEFT JOIN unit_alat u ON u.id = ri.unit_alat_id
                    LEFT JOIN model_alat m ON m.id = COALESCE(ri.model_alat_id, u.model_alat_id)
                    WHERE ri.rental_id = r.id
                ) items ON TRUE
                WHERE (%s = '' OR r.status = %s)
                  AND (%s = '' OR concat_ws(' ', r.nomor_rental,
                       kl.nama_perusahaan, p.kode, r.status, q.nomor,
                       r.lokasi_penggunaan) ILIKE %s)
                ORDER BY r.dibuat_pada DESC, r.id DESC
                LIMIT 500
                """,
                [status_filter, status_filter, search, f"%{search}%"],
            )
            rentals = [
                {
                    "id": row[0], "company": row[1], "number": row[2],
                    "client": row[3], "start": row[4], "end": row[5],
                    "status": row[6], "unit_count": row[7], "units": row[8],
                    "quotation_id": row[9], "quotation_number": row[10],
                    "invoice_id": row[11], "invoice_number": row[12],
                    "location": row[13], "latitude": row[14], "longitude": row[15],
                    "map_edit": row[14] is None or row[15] is None,
                }
                for row in cursor.fetchall()
            ]
    except DatabaseError:
        database_error = "Belum dapat membaca rekap rental. Periksa koneksi database."
    return render(
        request,
        "portal/rental_list.html",
        {"rentals": rentals, "database_error": database_error,
         "search": search, "status_filter": status_filter},
    )


@business_permission_required("RENTAL", "UBAH")
def rental_location_edit(request, rental_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.nomor_rental, kl.nama_perusahaan, r.status,
                       r.lokasi_penggunaan, r.latitude, r.longitude
                FROM rental r JOIN klien kl ON kl.id = r.klien_id
                WHERE r.id = %s
                """,
                [rental_id],
            )
            row = cursor.fetchone()
            if not row:
                raise Http404("Rental tidak ditemukan.")
            rental = {
                "id": rental_id, "number": row[0], "client": row[1],
                "status": row[2], "location": row[3] or "",
            }
            cursor.execute(
                """
                SELECT string_agg(DISTINCT concat_ws(' ', m.merek, m.nama_model,
                           CASE WHEN u.nomor_seri IS NOT NULL THEN 'S/N ' || u.nomor_seri END), ', ')
                FROM rental_item ri
                LEFT JOIN unit_alat u ON u.id = ri.unit_alat_id
                LEFT JOIN model_alat m ON m.id = COALESCE(ri.model_alat_id, u.model_alat_id)
                WHERE ri.rental_id = %s
                """,
                [rental_id],
            )
            rental["units"] = cursor.fetchone()[0] or "Belum ada unit"
    except DatabaseError:
        raise Http404("Data rental tidak dapat dibaca.")

    initial = {"location_name": row[3] or "", "latitude": row[4], "longitude": row[5]}
    form = RentalLocationForm(request.POST if request.method == "POST" else None, initial=initial)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE rental SET lokasi_penggunaan = %s, latitude = %s, longitude = %s
                    WHERE id = %s
                    """,
                    [data["location_name"] or None, data["latitude"], data["longitude"], rental_id],
                )
            messages.success(request, f"Lokasi penggunaan rental {rental['number']} berhasil diperbarui.")
            return redirect("rental_list")
        except DatabaseError:
            form.add_error(None, "Lokasi rental belum tersimpan. Periksa koordinat dan database.")

    return render(request, "portal/rental_location_form.html", {"form": form, "rental": rental})


@business_permission_required("PROYEK", "LIHAT")
def project_list(request):
    include_archived = request.GET.get("arsip") == "1"
    projects = []
    rental_usages = []
    database_error = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pr.id, co.kode, pr.kode_proyek, pr.nama,
                       kl.nama_perusahaan, pr.jenis_layanan,
                       pr.tanggal_mulai_rencana, pr.tanggal_selesai_rencana, pr.status,
                       pr.lokasi_teks, pr.latitude, pr.longitude
                FROM proyek pr
                JOIN perusahaan co ON co.id = pr.perusahaan_id
                JOIN klien kl ON kl.id = pr.klien_id
                WHERE (%s = TRUE OR pr.status <> 'DIARSIPKAN')
                ORDER BY pr.dibuat_pada DESC, pr.id DESC
                LIMIT 500
                """,
                [include_archived],
            )
            projects = [
                {"id": row[0], "company": row[1], "code": row[2], "name": row[3],
                 "client": row[4], "service": row[5], "start": row[6],
                 "end": row[7], "status": row[8], "location": row[9],
                 "latitude": float(row[10]) if row[10] is not None else None,
                 "longitude": float(row[11]) if row[11] is not None else None,
                 "equipment": []}
                for row in cursor.fetchall()
            ]
            if projects:
                project_ids = [project["id"] for project in projects]
                cursor.execute(
                    """
                    SELECT DISTINCT pu.proyek_id, u.id,
                           concat_ws(' ', m.merek, m.nama_model), u.nomor_seri,
                           r.nomor_rental, kl.nama_perusahaan
                    FROM proyek_unit pu
                    JOIN unit_alat u ON u.id = pu.unit_alat_id AND u.status = 'DISEWA'
                    JOIN model_alat m ON m.id = u.model_alat_id
                    JOIN rental_item ri ON ri.unit_alat_id = u.id
                    JOIN rental r ON r.id = ri.rental_id AND r.status = 'BERJALAN'
                    JOIN klien kl ON kl.id = r.klien_id
                    WHERE pu.proyek_id = ANY(%s)
                    ORDER BY pu.proyek_id, r.nomor_rental, m.nama_model
                    """,
                    [project_ids],
                )
                equipment_by_project = {}
                for project_id, unit_id, name, serial, rental_number, client_name in cursor.fetchall():
                    equipment_by_project.setdefault(project_id, []).append({
                        "id": unit_id, "name": name, "serial": serial or "-",
                        "rental_number": rental_number, "client": client_name,
                    })
                for project in projects:
                    project["equipment"] = equipment_by_project.get(project["id"], [])
            cursor.execute(
                """
                SELECT r.id, co.kode, r.nomor_rental, kl.nama_perusahaan,
                       r.tanggal_mulai_rencana, r.tanggal_selesai_rencana, r.status,
                       r.lokasi_penggunaan, r.latitude, r.longitude,
                       COALESCE(items.daftar_unit, 'Belum ada unit tercatat')
                FROM rental r
                JOIN perusahaan co ON co.id = r.perusahaan_id
                JOIN klien kl ON kl.id = r.klien_id
                LEFT JOIN LATERAL (
                    SELECT string_agg(DISTINCT concat_ws(' ', m.merek, m.nama_model,
                               CASE WHEN u.nomor_seri IS NOT NULL THEN 'S/N ' || u.nomor_seri END),
                               ', ' ORDER BY concat_ws(' ', m.merek, m.nama_model,
                               CASE WHEN u.nomor_seri IS NOT NULL THEN 'S/N ' || u.nomor_seri END)) AS daftar_unit
                    FROM rental_item ri
                    LEFT JOIN unit_alat u ON u.id = ri.unit_alat_id
                    LEFT JOIN model_alat m ON m.id = COALESCE(ri.model_alat_id, u.model_alat_id)
                    WHERE ri.rental_id = r.id
                ) items ON TRUE
                WHERE r.status IN ('BERJALAN', 'SELESAI')
                  AND r.latitude IS NOT NULL AND r.longitude IS NOT NULL
                ORDER BY r.tanggal_mulai_rencana DESC, r.id DESC
                LIMIT 1000
                """
            )
            rental_usages = [
                {"id": row[0], "company": row[1], "number": row[2], "client": row[3],
                 "start": row[4], "end": row[5], "status": row[6], "location": row[7] or "",
                 "latitude": float(row[8]), "longitude": float(row[9]), "units": row[10],
                 "edit_url": f"/rental/{row[0]}/lokasi/"}
                for row in cursor.fetchall()
            ]
    except DatabaseError:
        database_error = "Belum dapat membaca daftar proyek. Periksa koneksi database."
    return render(
        request,
        "portal/project_list.html",
        {"projects": projects, "database_error": database_error,
         "include_archived": include_archived,
         "project_map_data": [
             {"id": p["id"], "name": p["name"], "code": p["code"],
              "client": p["client"], "service": p["service"],
              "location": p["location"] or "", "status": p["status"],
              "latitude": p["latitude"], "longitude": p["longitude"],
              "equipment": p["equipment"],
              "edit_url": f"/proyek/{p['id']}/edit/"}
             for p in projects if p["latitude"] is not None and p["longitude"] is not None
         ], "rental_usages": rental_usages,
         "rental_map_data": rental_usages},
    )


@business_permission_required("PROYEK", lambda request, project_id=None: "BUAT" if project_id is None else "UBAH")
def project_form(request, project_id=None):
    original = None
    selected_unit_ids = []
    database_error = None
    if project_id is not None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT perusahaan_id, kode_proyek, nama, kl.nama_perusahaan,
                           jenis_layanan, lokasi_teks, latitude, longitude,
                           tanggal_mulai_rencana, tanggal_selesai_rencana, status, catatan
                    FROM proyek pr JOIN klien kl ON kl.id = pr.klien_id
                    WHERE pr.id = %s
                    """,
                    [project_id],
                )
                row = cursor.fetchone()
                if row:
                    original = {
                        "company_id": str(row[0]), "code": row[1], "name": row[2],
                        "client_name": row[3], "service": row[4], "location_text": row[5] or "",
                        "latitude": row[6], "longitude": row[7], "start": row[8],
                        "end": row[9], "status": row[10], "notes": row[11] or "",
                    }
                    cursor.execute(
                        "SELECT unit_alat_id FROM proyek_unit WHERE proyek_id = %s",
                        [project_id],
                    )
                    selected_unit_ids = [str(item[0]) for item in cursor.fetchall()]
        except DatabaseError:
            database_error = "Data proyek belum dapat dibaca. Periksa koneksi database dan jalankan migrasi koordinat."
        if original is None and database_error is None:
            raise Http404("Proyek tidak ditemukan.")

    try:
        form = ProjectForm(
            request.POST if request.method == "POST" else None,
            initial=original or {"status": "DIRENCANAKAN", "rented_unit_ids": selected_unit_ids},
            project_id=project_id,
        )
        if original and request.method != "POST":
            form.initial["rented_unit_ids"] = selected_unit_ids
    except DatabaseError:
        form = None
        database_error = database_error or "Daftar perusahaan atau alat rental aktif belum dapat dibaca."

    if request.method == "POST" and form and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM klien WHERE lower(nama_perusahaan) = lower(%s)",
                        [data["client_name"]],
                    )
                    client = cursor.fetchone()
                    if client:
                        client_id = client[0]
                    else:
                        cursor.execute(
                            "INSERT INTO klien (nama_perusahaan) VALUES (%s) RETURNING id",
                            [data["client_name"]],
                        )
                        client_id = cursor.fetchone()[0]

                    values = [
                        int(data["company_id"]), data["code"], data["name"], client_id,
                        data["service"], data["location_text"], data["latitude"],
                        data["longitude"], data["start"], data["end"], data["status"],
                        data["notes"] or None,
                    ]
                    if project_id is None:
                        cursor.execute(
                            """
                            INSERT INTO proyek
                                (perusahaan_id, kode_proyek, nama, klien_id, jenis_layanan,
                                 lokasi_teks, latitude, longitude, tanggal_mulai_rencana,
                                 tanggal_selesai_rencana, status, catatan, dibuat_oleh)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            RETURNING id
                            """,
                            values + [None],
                        )
                        saved_project_id = cursor.fetchone()[0]
                    else:
                        cursor.execute(
                            """
                            UPDATE proyek SET perusahaan_id=%s, kode_proyek=%s, nama=%s,
                                klien_id=%s, jenis_layanan=%s, lokasi_teks=%s, latitude=%s,
                                longitude=%s, tanggal_mulai_rencana=%s,
                                tanggal_selesai_rencana=%s, status=%s, catatan=%s
                            WHERE id=%s
                            """,
                            values + [project_id],
                        )
                        saved_project_id = project_id

                    cursor.execute("DELETE FROM proyek_unit WHERE proyek_id = %s", [saved_project_id])
                    for unit_id in data["rented_unit_ids"]:
                        cursor.execute(
                            """
                            INSERT INTO proyek_unit
                                (proyek_id, unit_alat_id, mulai_rencana, selesai_rencana, catatan)
                            VALUES (%s, %s, %s, %s, %s)
                            """,
                            [saved_project_id, int(unit_id), data["start"], data["end"],
                             "Unit rental yang ditautkan ke proyek."],
                        )
            messages.success(request, "Data proyek dan koordinat berhasil disimpan.")
            return redirect("project_list")
        except DatabaseError:
            form.add_error("code", "Proyek belum tersimpan. Periksa kode proyek, perusahaan, atau database.")

    return render(request, "portal/project_form.html", {
        "form": form, "database_error": database_error,
        "is_edit": project_id is not None, "project_id": project_id,
    })


@business_permission_required("PROYEK", "UBAH")
def archive_project(request, project_id, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    if action not in {"arsipkan", "pulihkan"}:
        raise Http404("Aksi tidak ditemukan.")
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT status FROM proyek WHERE id = %s FOR UPDATE", [project_id])
                row = cursor.fetchone()
                if not row:
                    raise Http404("Proyek tidak ditemukan.")
                old_status = row[0]
                new_status = old_status
                if action == "arsipkan":
                    if old_status == "DIARSIPKAN":
                        messages.info(request, "Proyek ini sudah diarsipkan.")
                    else:
                        new_status = "DIARSIPKAN"
                elif old_status != "DIARSIPKAN":
                    messages.error(request, "Proyek ini tidak sedang diarsipkan.")
                else:
                    cursor.execute(
                        """
                        SELECT COALESCE(
                            (SELECT nilai_sebelum->>'status'
                             FROM audit_perubahan
                             WHERE jenis_entitas = 'proyek' AND entitas_id = %s
                               AND aksi = 'UBAH_STATUS'
                               AND nilai_sesudah->>'status' = 'DIARSIPKAN'
                             ORDER BY waktu DESC LIMIT 1),
                            'DIRENCANAKAN'
                        )
                        """,
                        [project_id],
                    )
                    new_status = cursor.fetchone()[0]
                if new_status != old_status:
                    cursor.execute("UPDATE proyek SET status = %s WHERE id = %s", [new_status, project_id])
                    cursor.execute(
                        """
                        INSERT INTO audit_perubahan
                            (jenis_entitas, entitas_id, aksi, alasan,
                             nilai_sebelum, nilai_sesudah, metadata)
                        VALUES ('proyek', %s, 'UBAH_STATUS', %s,
                                %s::jsonb, %s::jsonb, %s::jsonb)
                        """,
                        [project_id, "Arsip proyek" if action == "arsipkan" else "Pemulihan proyek",
                         json.dumps({"status": old_status}), json.dumps({"status": new_status}),
                         json.dumps({"django_user": request.user.get_username()})],
                    )
                    messages.success(request, "Proyek berhasil diarsipkan." if action == "arsipkan" else "Proyek berhasil dipulihkan.")
    except DatabaseError:
        messages.error(request, "Perubahan arsip belum tersimpan. Periksa koneksi database.")
    return redirect("project_list")


@business_permission_required("SERAH_TERIMA", lambda request: "UBAH" if request.GET.get("kind") == "KEMBALI" else "BUAT")
def rental_handover(request):
    database_error = None
    try:
        form = RentalHandoverForm(
            request.POST or None,
            rental_id=request.GET.get("rental_id"),
            return_only=request.GET.get("kind") == "KEMBALI",
        )
    except DatabaseError:
        form = None
        database_error = "Belum dapat membaca transaksi rental atau daftar PIC internal."

    if request.method == "POST" and form and form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT ri.id, ri.rental_id, u.id, u.status, r.nomor_rental
                        FROM rental_item ri
                        JOIN rental r ON r.id = ri.rental_id
                        JOIN unit_alat u ON u.id = ri.unit_alat_id
                        WHERE ri.id = %s
                        FOR UPDATE OF ri, u
                        """,
                        [data["rental_item_id"]],
                    )
                    row = cursor.fetchone()
                    if not row:
                        form.add_error("rental_item_id", "Unit rental tidak ditemukan.")
                    else:
                        rental_item_id, rental_id, unit_id, old_status, rental_number = row
                        cursor.execute(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                FROM serah_terima_rental_item out_item
                                JOIN serah_terima_rental out_doc
                                  ON out_doc.id = out_item.serah_terima_id
                                WHERE out_item.rental_item_id = %s
                                  AND COALESCE(out_item.unit_alat_id, %s) = %s
                                  AND out_doc.jenis = 'KELUAR'
                                  AND NOT EXISTS (
                                      SELECT 1
                                      FROM serah_terima_rental_item in_item
                                      JOIN serah_terima_rental in_doc
                                        ON in_doc.id = in_item.serah_terima_id
                                      WHERE in_item.rental_item_id = out_item.rental_item_id
                                        AND COALESCE(in_item.unit_alat_id, %s) = %s
                                        AND in_doc.jenis = 'KEMBALI'
                                        AND in_doc.tanggal > out_doc.tanggal
                                  )
                            )
                            """,
                            [rental_item_id, unit_id, unit_id, unit_id, unit_id],
                        )
                        has_open_exit = cursor.fetchone()[0]
                        kind = data["kind"]
                        if kind == "KELUAR" and (has_open_exit or old_status != "TERSEDIA"):
                            form.add_error(
                                "rental_item_id",
                                "Unit ini belum tersedia. Pastikan sudah dikembalikan dan statusnya Tersedia.",
                            )
                        elif kind == "KEMBALI" and not has_open_exit:
                            form.add_error(
                                "rental_item_id",
                                "Belum ada catatan keluar yang masih aktif untuk unit ini.",
                            )
                        else:
                            cursor.execute(
                                """
                                INSERT INTO serah_terima_rental
                                    (rental_id, jenis, tanggal, pic_internal_id,
                                     penerima_nama, catatan)
                                VALUES (%s, %s, now(), %s, %s, %s)
                                RETURNING id
                                """,
                                [rental_id, kind, data["internal_user_id"],
                                 data["recipient_name"] or None, data["notes"] or None],
                            )
                            document_id = cursor.fetchone()[0]
                            cursor.execute(
                                """
                                INSERT INTO serah_terima_rental_item
                                    (serah_terima_id, rental_item_id, unit_alat_id, kondisi)
                                VALUES (%s, %s, %s, %s)
                                """,
                                [document_id, rental_item_id, unit_id, data["condition"]],
                            )
                            new_status = "DISEWA" if kind == "KELUAR" else "TERSEDIA"
                            cursor.execute(
                                "UPDATE unit_alat SET status = %s WHERE id = %s",
                                [new_status, unit_id],
                            )
                            if kind == "KEMBALI":
                                cursor.execute(
                                    "UPDATE rental_item SET kondisi_kembali = %s WHERE id = %s",
                                    [data["condition"], rental_item_id],
                                )
                                cursor.execute(
                                    """
                                    SELECT COUNT(*)
                                    FROM rental_item ri
                                    WHERE ri.rental_id = %s
                                      AND ri.unit_alat_id IS NOT NULL
                                      AND EXISTS (
                                          SELECT 1
                                          FROM serah_terima_rental_item out_item
                                          JOIN serah_terima_rental out_doc
                                            ON out_doc.id = out_item.serah_terima_id
                                          WHERE out_item.rental_item_id = ri.id
                                            AND COALESCE(out_item.unit_alat_id, ri.unit_alat_id) = ri.unit_alat_id
                                            AND out_doc.jenis = 'KELUAR'
                                            AND NOT EXISTS (
                                                SELECT 1
                                                FROM serah_terima_rental_item in_item
                                                JOIN serah_terima_rental in_doc
                                                  ON in_doc.id = in_item.serah_terima_id
                                                WHERE in_item.rental_item_id = ri.id
                                                  AND COALESCE(in_item.unit_alat_id, ri.unit_alat_id) = ri.unit_alat_id
                                                  AND in_doc.jenis = 'KEMBALI'
                                                  AND in_doc.tanggal > out_doc.tanggal
                                            )
                                      )
                                    """,
                                    [rental_id],
                                )
                                remaining_units = cursor.fetchone()[0]
                                if remaining_units == 0:
                                    cursor.execute(
                                        """
                                        UPDATE rental
                                        SET status = 'SELESAI',
                                            tanggal_mulai_aktual = LEAST(
                                                COALESCE(tanggal_mulai_aktual, CURRENT_DATE), CURRENT_DATE
                                            ),
                                            tanggal_selesai_aktual = CURRENT_DATE
                                        WHERE id = %s
                                        """,
                                        [rental_id],
                                    )
                            cursor.execute(
                                """
                                INSERT INTO audit_perubahan
                                    (jenis_entitas, entitas_id, aksi, alasan,
                                     nilai_sebelum, nilai_sesudah, metadata)
                                VALUES ('unit_alat', %s, 'UBAH_STATUS', %s,
                                        %s::jsonb, %s::jsonb, %s::jsonb)
                                """,
                                [unit_id, f"Serah-terima rental {rental_number} ({kind}).",
                                 json.dumps({"status": old_status}),
                                 json.dumps({"status": new_status, "rental_number": rental_number}),
                                 json.dumps({"django_user": request.user.get_username()})],
                            )
                            messages.success(
                                request,
                                f"Serah-terima {kind.lower()} tersimpan; status alat menjadi {new_status}.",
                            )
                            if request.GET.get("rental_id"):
                                return redirect("rental_list")
                            return redirect("inventory")
        except DatabaseError as exc:
            logger.exception("Serah-terima rental gagal disimpan")
            detail = str(exc).splitlines()[0][:300]
            form.add_error(None, f"Serah-terima belum tersimpan. Detail database: {detail}")

    if form and (not form.fields["rental_item_id"].choices or not form.fields["internal_user_id"].choices):
        database_error = (
            "Agar bisa mencatat serah-terima, database perlu memiliki transaksi rental dengan unit fisik "
            "serta data PIC di tabel pengguna."
        )
    return render(
        request,
        "portal/rental_handover.html",
        {"form": form, "database_error": database_error},
    )


@business_permission_required("RENTAL", "LIHAT")
def place_search(request):
    """Geocode one explicit user search, with caching and a global 1 req/sec limit."""
    global _place_search_last_request
    query = request.GET.get("q", "").strip()[:180]
    if len(query) < 3:
        return JsonResponse({"results": [], "message": "Ketik minimal 3 karakter."}, status=400)
    cache_key = "place-search:" + hashlib.sha256(query.casefold().encode("utf-8")).hexdigest()
    cached_results = cache.get(cache_key)
    if cached_results is not None:
        return JsonResponse({"results": cached_results, "cached": True})

    try:
        with _place_search_lock:
            wait = 1.0 - (time.monotonic() - _place_search_last_request)
            if wait > 0:
                time.sleep(wait)
            params = urllib.parse.urlencode({
                "q": query, "format": "jsonv2", "limit": 5,
                "countrycodes": "id", "accept-language": "id,en",
            })
            request_to_provider = urllib.request.Request(
                f"{settings.GEOCODING_SEARCH_URL}?{params}",
                headers={
                    "User-Agent": "Seanav-Operations/1.0 (internal rental and project location search)",
                    "Accept": "application/json",
                },
            )
            _place_search_last_request = time.monotonic()
            with urllib.request.urlopen(request_to_provider, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        results = [
            {"name": str(item.get("display_name", ""))[:500],
             "latitude": item.get("lat"), "longitude": item.get("lon")}
            for item in payload if item.get("display_name") and item.get("lat") and item.get("lon")
        ]
        cache.set(cache_key, results, 7 * 24 * 60 * 60)
        return JsonResponse({"results": results, "cached": False})
    except Exception:
        logger.exception("Pencarian lokasi rental gagal")
        return JsonResponse(
            {"results": [], "message": "Pencarian lokasi gagal. Coba lagi atau tandai titik langsung pada peta."},
            status=503,
        )


@business_permission_required("RENTAL", "BUAT")
def rental_create(request):
    database_error = None
    invoice_error = None
    quote_initial = {}
    accepted_quote = None
    quote_preview = []
    equipment_lines = []
    invoice_choices = []
    linked_rentals = []
    manual_mode = request.GET.get("manual") == "1"
    requested_quote_id = (request.POST.get("quotation_id") if request.method == "POST"
                          else request.GET.get("quotation_id"))
    requested_invoice_id = (request.POST.get("invoice_id") if request.method == "POST"
                            else request.GET.get("invoice_id"))
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT i.id, i.nomor, q.nomor, co.kode, kl.nama_perusahaan,
                       i.status, i.total
                FROM invoice i
                JOIN quotation q ON q.id = i.quotation_id
                JOIN perusahaan co ON co.id = i.perusahaan_id
                JOIN klien kl ON kl.id = q.klien_id
                WHERE q.jenis = 'RENTAL' AND q.status = 'DITERIMA_KLIEN'
                  AND i.status IN ('DITERBITKAN', 'DIBAYAR')
                  AND NOT EXISTS (SELECT 1 FROM rental r WHERE r.invoice_id = i.id
                                  OR r.quotation_id = q.id)
                ORDER BY i.dibuat_pada DESC, i.id DESC
                """
            )
            invoice_choices = cursor.fetchall()
            cursor.execute(
                """
                SELECT r.id, r.nomor_rental, q.id, q.nomor, i.id, i.nomor,
                       kl.nama_perusahaan
                FROM rental r
                LEFT JOIN quotation q ON q.id = r.quotation_id
                LEFT JOIN invoice i ON i.id = r.invoice_id
                JOIN klien kl ON kl.id = r.klien_id
                WHERE r.quotation_id IS NOT NULL OR r.invoice_id IS NOT NULL
                ORDER BY r.dibuat_pada DESC, r.id DESC
                LIMIT 20
                """
            )
            linked_rentals = cursor.fetchall()
        if requested_invoice_id and str(requested_invoice_id).isdigit():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT i.quotation_id,
                           EXISTS (SELECT 1 FROM rental r WHERE r.invoice_id = i.id
                                   OR r.quotation_id = i.quotation_id),
                           i.status, q.status
                    FROM invoice i JOIN quotation q ON q.id = i.quotation_id
                    WHERE i.id = %s
                    """,
                    [requested_invoice_id],
                )
                invoice_quote = cursor.fetchone()
            if invoice_quote:
                requested_quote_id = str(invoice_quote[0])
                if invoice_quote[1]:
                    invoice_error = "Invoice dan quotation ini sudah terhubung ke rental. Buka rekap Rental Alat untuk melihat data yang sudah dibuat."
                elif invoice_quote[2] not in ("DITERBITKAN", "DIBAYAR") or invoice_quote[3] != "DITERIMA_KLIEN":
                    invoice_error = "Rental hanya bisa dibuat dari quotation yang disetujui dan invoice yang sudah diterbitkan atau dibayar."
            else:
                invoice_error = "Invoice yang dipilih tidak ditemukan. Pilih invoice yang tersedia di bawah."
        if requested_quote_id and str(requested_quote_id).isdigit():
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT q.id, q.perusahaan_id, q.nomor, kl.nama_perusahaan,
                           MIN(qi.tanggal_mulai_rencana), MAX(qi.tanggal_selesai_rencana),
                           inv.id, inv.nomor, inv.total
                    FROM quotation q
                    JOIN klien kl ON kl.id = q.klien_id
                    JOIN invoice inv ON inv.quotation_id = q.id
                    LEFT JOIN quotation_item qi ON qi.quotation_id = q.id
                    WHERE q.id = %s AND q.jenis = 'RENTAL'
                      AND q.status = 'DITERIMA_KLIEN'
                      AND inv.status IN ('DITERBITKAN', 'DIBAYAR')
                      AND NOT EXISTS (SELECT 1 FROM rental r WHERE r.quotation_id = q.id)
                    GROUP BY q.id, q.perusahaan_id, q.nomor, kl.nama_perusahaan,
                             inv.id, inv.nomor, inv.total
                    """,
                    [requested_quote_id],
                )
                accepted_quote = cursor.fetchone()
                quote_equipment = []
                if accepted_quote:
                    cursor.execute(
                        """
                        SELECT qi.id, qi.model_alat_id, qi.deskripsi_item, qi.kuantitas,
                               m.merek, m.nama_model, qi.harga_satuan, qi.subtotal
                        FROM quotation_item qi
                        LEFT JOIN model_alat m ON m.id = qi.model_alat_id
                        WHERE qi.quotation_id = %s
                        ORDER BY qi.id
                        """,
                        [requested_quote_id],
                    )
                    quote_equipment = cursor.fetchall()
            if accepted_quote:
                quote_preview = []
                for item in quote_equipment:
                    line = {
                        "id": item[0], "model_id": item[1], "description": item[2],
                        "quantity": item[3], "brand": item[4], "model": item[5],
                        "unit_price": item[6], "total": item[7],
                        "unit_price_display": f"{Decimal(item[6] or 0):,.0f}".replace(",", "."),
                        "total_display": f"{Decimal(item[7] or 0):,.0f}".replace(",", "."),
                        "units": [],
                    }
                    if item[1] is not None:
                        quantity = Decimal(item[3])
                        if quantity != quantity.to_integral_value():
                            line["error"] = "Jumlah unit inventaris harus bilangan bulat."
                        else:
                            equipment_lines.append(line)
                    quote_preview.append(line)

                # Pilih unit TERSEDIA otomatis sesuai model dan kuantitas tiap baris quotation.
                # Hanya dilakukan untuk quotation accepted; item manual tetap tampil sebagai preview.
                if equipment_lines:
                    with connection.cursor() as cursor:
                        model_ids = list(dict.fromkeys(line["model_id"] for line in equipment_lines))
                        for model_id in model_ids:
                            matching_lines = [
                                line for line in equipment_lines if line["model_id"] == model_id
                            ]
                            required = sum(int(Decimal(line["quantity"])) for line in matching_lines)
                            cursor.execute(
                                """
                                SELECT u.id, u.nomor_seri, u.kode_inventaris
                                FROM unit_alat u
                                WHERE u.model_alat_id = %s AND u.status = 'TERSEDIA'
                                ORDER BY u.id
                                LIMIT %s
                                """,
                                [model_id, required],
                            )
                            available_units = cursor.fetchall()
                            offset = 0
                            for line in matching_lines:
                                quantity = int(Decimal(line["quantity"]))
                                line["units"] = available_units[offset:offset + quantity]
                                line["shortage"] = len(line["units"]) < quantity
                                offset += quantity

                quote_initial = {
                    "quotation_id": accepted_quote[0],
                    "company_id": accepted_quote[1],
                    "rental_number": accepted_quote[2],
                    "client_name": accepted_quote[3],
                    "quotation_reference": accepted_quote[2],
                    "rental_start": accepted_quote[4],
                    "rental_end": accepted_quote[5] or accepted_quote[4],
                }
        form = RentalCreateForm(request.POST or None, initial=quote_initial)
        if accepted_quote:
            # Unit ditetapkan di server saat submit, sehingga tidak perlu dipilih ulang di form.
            form.fields["unit_ids"].widget = forms.MultipleHiddenInput()
            form.fields["unit_ids"].required = False
            form.fields["unit_ids"].choices = []
            if request.method == "POST":
                form.fields["unit_ids"].choices = [
                    (str(unit[0]), str(unit[0]))
                    for line in equipment_lines for unit in line["units"]
                ]
                form.initial["unit_ids"] = [str(unit[0]) for line in equipment_lines for unit in line["units"]]
    except DatabaseError:
        form = None
        database_error = "Belum dapat membaca daftar perusahaan atau unit dari database."

    if request.method == "POST" and form and form.is_valid():
        data = form.cleaned_data
        selected_unit_ids = sorted({int(unit_id) for unit_id in data["unit_ids"]})
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    quotation_id = data.get("quotation_id")
                    quotation_valid = True
                    quotation_total = Decimal("0")
                    invoice_id = None
                    rental_quote_items = []
                    if quotation_id:
                        cursor.execute(
                            """
                            SELECT q.perusahaan_id, kl.nama_perusahaan, inv.total, inv.id
                            FROM quotation q JOIN klien kl ON kl.id = q.klien_id
                            JOIN invoice inv ON inv.quotation_id = q.id
                            WHERE q.id = %s AND q.jenis = 'RENTAL'
                              AND q.status = 'DITERIMA_KLIEN'
                              AND inv.status IN ('DITERBITKAN', 'DIBAYAR')
                              AND NOT EXISTS (SELECT 1 FROM rental r WHERE r.quotation_id = q.id)
                            FOR UPDATE OF q
                            """,
                            [quotation_id],
                        )
                        quote = cursor.fetchone()
                        if (not quote or str(quote[0]) != str(data["company_id"])
                                or quote[1].strip().casefold() != data["client_name"].strip().casefold()):
                            quotation_valid = False
                            form.add_error(None, "Quotation harus disetujui dan invoicenya sudah diterbitkan sebelum rental dibuat.")
                        else:
                            quotation_total = quote[2]
                            invoice_id = quote[3]
                            cursor.execute(
                                """
                                SELECT qi.id, qi.model_alat_id, qi.kuantitas, qi.harga_satuan
                                FROM quotation_item qi
                                WHERE qi.quotation_id = %s AND qi.model_alat_id IS NOT NULL
                                ORDER BY qi.id
                                """,
                                [quotation_id],
                            )
                            rental_quote_items = cursor.fetchall()
                            expected = {}
                            invalid_quantity = False
                            for quote_item_id, model_id, quantity, unit_tariff in rental_quote_items:
                                quantity = Decimal(quantity)
                                if quantity != quantity.to_integral_value():
                                    invalid_quantity = True
                                    break
                                expected.setdefault(model_id, []).append(
                                    (quote_item_id, int(quantity), unit_tariff)
                                )
                            if invalid_quantity:
                                quotation_valid = False
                                form.add_error(None, "Jumlah unit alat pada quotation harus bilangan bulat agar bisa dipasangkan dengan unit inventaris.")
                            elif not expected:
                                quotation_valid = False
                                form.add_error(None, "Quotation ini tidak memiliki item alat yang terhubung ke inventaris.")
                            else:
                                units = []
                                assignment = {}
                                for model_id, lines in expected.items():
                                    required = sum(qty for _, qty, _ in lines)
                                    cursor.execute(
                                        """
                                        SELECT id, model_alat_id, status FROM unit_alat
                                        WHERE model_alat_id = %s AND status = 'TERSEDIA'
                                        ORDER BY id LIMIT %s FOR UPDATE
                                        """,
                                        [model_id, required],
                                    )
                                    model_units = cursor.fetchall()
                                    if len(model_units) < required:
                                        quotation_valid = False
                                        form.add_error(None, "Stok unit tersedia tidak cukup untuk memenuhi semua alat di quotation. Periksa ketersediaan inventaris.")
                                        break
                                    units.extend(model_units)
                                    offset = 0
                                    for quote_item_id, quantity, unit_tariff in lines:
                                        for unit in model_units[offset:offset + quantity]:
                                            assignment[unit[0]] = (quote_item_id, unit_tariff)
                                        offset += quantity
                                if quotation_valid:
                                    selected_unit_ids = [unit[0] for unit in units]
                                    quote_item_by_unit = assignment
                    else:
                        if not selected_unit_ids:
                            form.add_error("unit_ids", "Pilih setidaknya satu unit alat.")
                        else:
                            placeholders = ", ".join(["%s"] * len(selected_unit_ids))
                            cursor.execute(
                                f"SELECT id, model_alat_id, status FROM unit_alat "
                                f"WHERE id IN ({placeholders}) ORDER BY id FOR UPDATE",
                                selected_unit_ids,
                            )
                            units = cursor.fetchall()
                            quote_item_by_unit = {}
                    if not quotation_valid:
                        pass
                    elif form.errors:
                        pass
                    elif len(units) != len(selected_unit_ids) or any(unit[2] != "TERSEDIA" for unit in units):
                        form.add_error("unit_ids", "Ada unit yang sudah tidak tersedia. Muat ulang halaman dan pilih ulang unit.")
                    else:
                        cursor.execute(
                            "SELECT id FROM klien WHERE lower(nama_perusahaan) = lower(%s)",
                            [data["client_name"]],
                        )
                        client = cursor.fetchone()
                        if client:
                            client_id = client[0]
                        else:
                            cursor.execute(
                                "INSERT INTO klien (nama_perusahaan) VALUES (%s) RETURNING id",
                                [data["client_name"]],
                            )
                            client_id = cursor.fetchone()[0]

                        cursor.execute("SELECT id FROM pengguna WHERE lower(email) = lower(%s)", [data["pic_email"]])
                        pic = cursor.fetchone()
                        if pic:
                            pic_id = pic[0]
                            cursor.execute(
                                "UPDATE pengguna SET nama = %s, aktif = TRUE WHERE id = %s",
                                [data["pic_name"], pic_id],
                            )
                        else:
                            cursor.execute(
                                """
                                INSERT INTO pengguna (nama, email, password_hash)
                                VALUES (%s, %s, %s) RETURNING id
                                """,
                                [data["pic_name"], data["pic_email"], make_password(None)],
                            )
                            pic_id = cursor.fetchone()[0]

                        note = (
                            f"Referensi quotation: {data['quotation_reference']}"
                            if data["quotation_reference"] else None
                        )
                        cursor.execute(
                            """
                            INSERT INTO rental
                                (perusahaan_id, nomor_rental, quotation_id, invoice_id, klien_id,
                                 tanggal_mulai_rencana, tanggal_selesai_rencana,
                                 tanggal_mulai_aktual, status, total, catatan,
                                 lokasi_penggunaan, latitude, longitude)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'BERJALAN', %s, %s, %s, %s, %s)
                            RETURNING id
                            """,
                            [data["company_id"], data["rental_number"], quotation_id, invoice_id, client_id,
                             data["rental_start"], data["rental_end"],
                             # Rental creation records an immediate handover, so actual start is today.
                             date.today(),
                             quotation_total, note, data["location_name"] or None,
                             data["latitude"], data["longitude"]],
                        )
                        rental_id = cursor.fetchone()[0]
                        do_note = (
                            f"Delivery Order: {data['delivery_order']}"
                            if data["delivery_order"] else None
                        )
                        cursor.execute(
                            """
                            INSERT INTO serah_terima_rental
                                (rental_id, jenis, tanggal, pic_internal_id,
                                 penerima_nama, catatan)
                            VALUES (%s, 'KELUAR', now(), %s, %s, %s)
                            RETURNING id
                            """,
                            [rental_id, pic_id, data["recipient_name"] or None, do_note],
                        )
                        handover_id = cursor.fetchone()[0]
                        for unit in units:
                            cursor.execute(
                                """
                                INSERT INTO rental_item
                                    (rental_id, quotation_item_id, model_alat_id, unit_alat_id,
                                     kuantitas, tarif)
                                VALUES (%s, %s, %s, %s, 1, %s) RETURNING id
                                """,
                                [rental_id,
                                 quote_item_by_unit.get(unit[0], (None, Decimal("0")))[0],
                                 unit[1], unit[0],
                                 quote_item_by_unit.get(unit[0], (None, Decimal("0")))[1]],
                            )
                            rental_item_id = cursor.fetchone()[0]
                            cursor.execute(
                                """
                                INSERT INTO serah_terima_rental_item
                                    (serah_terima_id, rental_item_id, unit_alat_id, kondisi)
                                VALUES (%s, %s, %s, %s)
                                """,
                                [handover_id, rental_item_id, unit[0], data["condition"]],
                            )
                            cursor.execute("UPDATE unit_alat SET status = 'DISEWA' WHERE id = %s", [unit[0]])
                            cursor.execute(
                                """
                                INSERT INTO riwayat_unit
                                    (unit_alat_id, jenis, nilai_lama, nilai_baru,
                                     referensi_tipe, referensi_id, dicatat_oleh, catatan)
                                VALUES (%s, 'STATUS', %s, 'DISEWA', 'RENTAL', %s, %s, %s)
                                """,
                                [unit[0], unit[2], rental_id, pic_id, f"Rental {data['rental_number']}"],
                            )
                            cursor.execute(
                                """
                                INSERT INTO audit_perubahan
                                    (jenis_entitas, entitas_id, aksi, pengguna_id, alasan,
                                     nilai_sebelum, nilai_sesudah, metadata)
                                VALUES ('unit_alat', %s, 'UBAH_STATUS', %s, %s,
                                        %s::jsonb, %s::jsonb, %s::jsonb)
                                """,
                                [unit[0], pic_id, f"Unit diserahkan untuk rental {data['rental_number']}.",
                                 json.dumps({"status": unit[2]}), json.dumps({"status": "DISEWA"}),
                                 json.dumps({"django_user": request.user.get_username()})],
                            )
                        messages.success(request, f"Rental berhasil dicatat dengan {len(units)} unit; serah-terima keluar tersimpan.")
                        return redirect("rental_list")
        except DatabaseError:
            form.add_error(None, "Rental belum tersimpan. Pastikan nomor rental belum dipakai perusahaan ini.")

    if form and not form.fields["company_id"].choices:
        database_error = "Pastikan perusahaan sudah terdaftar dan ada unit berstatus TERSEDIA."
    return render(request, "portal/rental_create.html", {
        "form": form, "database_error": database_error,
        "quote_preview": quote_preview if accepted_quote else [],
        "accepted_quote": accepted_quote if accepted_quote else None,
        "accepted_invoice_id": accepted_quote[6] if accepted_quote else None,
        "accepted_invoice_number": accepted_quote[7] if accepted_quote else None,
        "accepted_invoice_total": accepted_quote[8] if accepted_quote else None,
        "accepted_invoice_total_display": (
            f"{Decimal(accepted_quote[8] or 0):,.0f}".replace(",", ".")
            if accepted_quote else None
        ),
        "invoice_choices": invoice_choices,
        "linked_rentals": linked_rentals,
        "invoice_error": invoice_error,
        "manual_mode": manual_mode,
    })


@business_permission_required("INVENTARIS", "BUAT")
def add_inventory(request):
    form = EquipmentForm(request.POST or None, request.FILES or None)
    accessory_formset = AccessoryFormSet(request.POST or None, prefix="accessories")
    form_valid = form.is_valid() if request.method == "POST" else False
    accessories_valid = accessory_formset.is_valid() if request.method == "POST" else False
    if request.method == "POST" and form_valid and accessories_valid:
        data = form.cleaned_data
        accessory_items = [
            (item.cleaned_data["name"], item.cleaned_data["quantity"], item.cleaned_data["serial_number"])
            for item in accessory_formset.forms
            if item.cleaned_data and item.cleaned_data.get("name")
            and not item.cleaned_data.get("DELETE")
        ]
        photo_path = None
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM kategori_alat WHERE lower(nama) = lower(%s)",
                        [data["category"]],
                    )
                    category_row = cursor.fetchone()
                    if category_row:
                        category_id = category_row[0]
                    else:
                        cursor.execute(
                            "INSERT INTO kategori_alat (nama) VALUES (%s) RETURNING id",
                            [data["category"]],
                        )
                        category_id = cursor.fetchone()[0]

                    cursor.execute(
                        """
                        SELECT id FROM model_alat
                        WHERE kategori_alat_id = %s
                          AND lower(nama_model) = lower(%s)
                          AND COALESCE(lower(merek), '') = lower(%s)
                        """,
                        [category_id, data["model_name"], data["brand"]],
                    )
                    model_row = cursor.fetchone()
                    if model_row:
                        model_id = model_row[0]
                    else:
                        cursor.execute(
                            """
                            INSERT INTO model_alat
                                (kategori_alat_id, merek, nama_model)
                            VALUES (%s, %s, %s) RETURNING id
                            """,
                            [category_id, data["brand"] or None, data["model_name"]],
                        )
                        model_id = cursor.fetchone()[0]

                    location_id = None
                    if data["location"]:
                        cursor.execute(
                            "SELECT id FROM lokasi WHERE lower(nama) = lower(%s)",
                            [data["location"]],
                        )
                        location_row = cursor.fetchone()
                        if location_row:
                            location_id = location_row[0]
                        else:
                            cursor.execute(
                                "INSERT INTO lokasi (nama, jenis) VALUES (%s, 'GUDANG') RETURNING id",
                                [data["location"]],
                            )
                            location_id = cursor.fetchone()[0]

                    photo = data.get("photo")
                    if photo:
                        photo_path = default_storage.save(f"inventory/{photo.name}", photo)

                    cursor.execute(
                        """
                        INSERT INTO unit_alat
                            (model_alat_id, nomor_seri, om_sn, foto_url, kondisi, status, lokasi_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        RETURNING id
                        """,
                        [
                            model_id,
                            data["serial_number"] or None,
                            data["om_sn"] or None,
                            photo_path,
                            data["condition"],
                            data["status"],
                            location_id,
                        ],
                    )
                    unit_id = cursor.fetchone()[0]
                    _sync_unit_accessories(cursor, unit_id, accessory_items)
                    cursor.execute(
                        """
                        INSERT INTO audit_perubahan
                            (jenis_entitas, entitas_id, aksi, pengguna_id,
                             nilai_sesudah, metadata)
                        VALUES ('unit_alat', %s, 'BUAT', NULL, %s::jsonb, %s::jsonb)
                        """,
                        [
                            unit_id,
                            json.dumps(
                                {
                                    "category": data["category"],
                                    "brand": data["brand"],
                                    "model": data["model_name"],
                                    "serial_number": data["serial_number"],
                                    "om_sn": data["om_sn"],
                                    "accessories": [
                                        {"name": name, "quantity": str(quantity), "serial_number": serial_number}
                                        for name, quantity, serial_number in accessory_items
                                    ],
                                    "status": data["status"],
                                }
                            ),
                            json.dumps({"django_user": request.user.get_username()}),
                        ],
                    )
            messages.success(request, "Unit alat berhasil ditambahkan ke inventaris bersama.")
            return redirect("inventory")
        except DatabaseError:
            if photo_path:
                default_storage.delete(photo_path)
            form.add_error(None, "Data belum tersimpan. Periksa database atau serial number duplikat.")

    return render(
        request, "portal/inventory_form.html",
        {"form": form, "accessory_formset": accessory_formset},
    )


@business_permission_required("INVENTARIS", "UBAH")
def edit_inventory(request, unit_id):
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT u.id, k.nama, m.merek, m.nama_model, u.nomor_seri,
                       u.om_sn, u.kondisi, u.status, l.nama, u.foto_url,
                       COALESCE((
                           SELECT json_agg(json_build_object(
                               'name', pai.nama_item,
                               'quantity', pui.jumlah::text,
                               'serial_number', pui.nomor_seri
                           ) ORDER BY pai.nama_item, pui.id)::text
                           FROM paket_unit pu
                           JOIN paket_unit_item pui ON pui.paket_unit_id = pu.id
                           JOIN paket_alat_item pai ON pai.id = pui.paket_alat_item_id
                           WHERE pu.unit_utama_id = u.id
                       ), '')
                FROM unit_alat AS u
                JOIN model_alat AS m ON m.id = u.model_alat_id
                JOIN kategori_alat AS k ON k.id = m.kategori_alat_id
                LEFT JOIN lokasi AS l ON l.id = u.lokasi_id
                WHERE u.id = %s
                """,
                [unit_id],
            )
            row = cursor.fetchone()
    except DatabaseError:
        row = None

    if row is None:
        raise Http404("Unit alat tidak ditemukan.")

    original = {
        "id": row[0],
        "category": row[1],
        "brand": row[2] or "",
        "model_name": row[3],
        "serial_number": row[4] or "",
        "om_sn": row[5] or "",
        "condition": row[6],
        "status": row[7],
        "location": row[8] or "",
        "photo_path": row[9],
        "accessories": json.loads(row[10] or "[]"),
    }

    if request.method == "POST":
        form = EquipmentForm(request.POST, request.FILES)
        accessory_formset = AccessoryFormSet(request.POST, prefix="accessories")
    else:
        form = EquipmentForm(initial=original)
        accessory_formset = AccessoryFormSet(
            prefix="accessories",
            initial=original["accessories"],
        )

    form_valid = form.is_valid() if request.method == "POST" else False
    accessories_valid = accessory_formset.is_valid() if request.method == "POST" else False
    if request.method == "POST" and form_valid and accessories_valid:
        data = form.cleaned_data
        accessory_items = [
            (item.cleaned_data["name"], item.cleaned_data["quantity"], item.cleaned_data["serial_number"])
            for item in accessory_formset.forms
            if item.cleaned_data and item.cleaned_data.get("name")
            and not item.cleaned_data.get("DELETE")
        ]
        new_photo_path = None
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT id FROM kategori_alat WHERE lower(nama) = lower(%s)",
                        [data["category"]],
                    )
                    category_row = cursor.fetchone()
                    if category_row:
                        category_id = category_row[0]
                    else:
                        cursor.execute(
                            "INSERT INTO kategori_alat (nama) VALUES (%s) RETURNING id",
                            [data["category"]],
                        )
                        category_id = cursor.fetchone()[0]

                    cursor.execute(
                        """
                        SELECT id FROM model_alat
                        WHERE kategori_alat_id = %s
                          AND lower(nama_model) = lower(%s)
                          AND COALESCE(lower(merek), '') = lower(%s)
                        """,
                        [category_id, data["model_name"], data["brand"]],
                    )
                    model_row = cursor.fetchone()
                    if model_row:
                        model_id = model_row[0]
                    else:
                        cursor.execute(
                            """
                            INSERT INTO model_alat
                                (kategori_alat_id, merek, nama_model)
                            VALUES (%s, %s, %s) RETURNING id
                            """,
                            [category_id, data["brand"] or None, data["model_name"]],
                        )
                        model_id = cursor.fetchone()[0]

                    location_id = None
                    if data["location"]:
                        cursor.execute(
                            "SELECT id FROM lokasi WHERE lower(nama) = lower(%s)",
                            [data["location"]],
                        )
                        location_row = cursor.fetchone()
                        if location_row:
                            location_id = location_row[0]
                        else:
                            cursor.execute(
                                "INSERT INTO lokasi (nama, jenis) VALUES (%s, 'GUDANG') RETURNING id",
                                [data["location"]],
                            )
                            location_id = cursor.fetchone()[0]

                    new_photo_path = original["photo_path"]
                    photo = data.get("photo")
                    if photo:
                        new_photo_path = default_storage.save(f"inventory/{photo.name}", photo)

                    cursor.execute(
                        """
                        UPDATE unit_alat
                        SET model_alat_id = %s, nomor_seri = %s, om_sn = %s,
                            foto_url = %s, kondisi = %s, status = %s, lokasi_id = %s
                        WHERE id = %s
                        """,
                        [
                            model_id,
                            data["serial_number"] or None,
                            data["om_sn"] or None,
                            new_photo_path,
                            data["condition"],
                            data["status"],
                            location_id,
                            unit_id,
                        ],
                    )
                    _sync_unit_accessories(cursor, unit_id, accessory_items)

                    after = {
                        "category": data["category"],
                        "brand": data["brand"],
                        "model": data["model_name"],
                        "serial_number": data["serial_number"],
                        "om_sn": data["om_sn"],
                        "condition": data["condition"],
                        "status": data["status"],
                        "location": data["location"],
                        "photo_url": new_photo_path,
                        "accessories": [
                            {"name": name, "quantity": str(quantity), "serial_number": serial_number}
                            for name, quantity, serial_number in accessory_items
                        ],
                    }
                    before = {
                        "category": original["category"],
                        "brand": original["brand"],
                        "model": original["model_name"],
                        "serial_number": original["serial_number"],
                        "om_sn": original["om_sn"],
                        "condition": original["condition"],
                        "status": original["status"],
                        "location": original["location"],
                        "photo_url": original["photo_path"],
                        "accessories": [
                            {"name": item["name"], "quantity": str(item["quantity"]),
                             "serial_number": item.get("serial_number") or ""}
                            for item in original["accessories"]
                        ],
                    }
                    cursor.execute(
                        """
                        INSERT INTO audit_perubahan
                            (jenis_entitas, entitas_id, aksi, pengguna_id,
                             alasan, nilai_sebelum, nilai_sesudah, metadata)
                        VALUES ('unit_alat', %s, 'UBAH', NULL, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                        """,
                        [
                            unit_id,
                            "Perubahan melalui form inventaris.",
                            json.dumps(before),
                            json.dumps(after),
                            json.dumps({"django_user": request.user.get_username()}),
                        ],
                    )

            if photo and original["photo_path"] and original["photo_path"] != new_photo_path:
                default_storage.delete(original["photo_path"])
            messages.success(request, "Data unit berhasil diperbarui.")
            return redirect("inventory")
        except DatabaseError:
            if new_photo_path and new_photo_path != original["photo_path"]:
                default_storage.delete(new_photo_path)
            form.add_error(None, "Perubahan belum tersimpan. Periksa database atau serial number duplikat.")

    return render(
        request,
        "portal/inventory_form.html",
        {"form": form, "accessory_formset": accessory_formset, "is_edit": True, "unit_id": unit_id, "current_photo": default_storage.url(original["photo_path"]) if original["photo_path"] else None},
    )




def _document_template(name):
    """Cari template Word: webapp/document_templates/ (utama), lalu folder Database/ (lama)."""
    base = Path(__file__).resolve().parents[1]
    for folder in (base / "document_templates", base.parent):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return base / "document_templates" / name


def page_not_found(request, exception=None):
    return render(request, "portal/error.html", {
        "code": 404, "title": "Halaman tidak ditemukan",
        "message": "Alamat yang Anda buka tidak tersedia atau sudah dipindahkan.",
    }, status=404)


def server_error(request):
    from django.template import loader
    template = loader.get_template("portal/error.html")
    html = template.render({
        "code": 500, "title": "Terjadi kesalahan sistem",
        "message": "Permintaan belum dapat diproses. Coba lagi beberapa saat; jika berulang, hubungi administrator.",
    })
    return HttpResponse(html, status=500)


def permission_denied_view(request, exception=None):
    return render(request, "portal/permission_denied.html",
                  {"database_error": False, "source": "halaman ini", "action": "akses"}, status=403)
