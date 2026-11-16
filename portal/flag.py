"""Pinjam Bendera (LPJ) — dokumen atas nama perusahaan untuk pertanggungjawaban klien / peneliti.

Data berdiri sendiri: tidak membuat quotation, invoice modul, rental, atau mengubah status alat.
Dokumen yang bisa dicetak: Invoice, Kwitansi, dan Berita Acara Serah Terima (BAST).
Harga default sudah termasuk PPN (DPP = Jumlah / 1,11). Fee perusahaan dicatat terpisah
(tidak tercetak di dokumen klien).
"""
import io
import logging
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib import messages
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from .doc_builder import fmt_idr
from .lpj_builder import build_lpj_bast, build_lpj_invoice, build_lpj_receipt
from .document_views import _brand_code, _respond, file_name
from .views import business_permission_required

logger = logging.getLogger(__name__)

STATUS_LABELS = {"DRAFT": "Draft", "DOKUMEN_TERBIT": "Dokumen terbit", "FEE_DITERIMA": "Fee diterima",
                 "DIBATALKAN": "Dibatalkan"}
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
         "Oktober", "November", "Desember"]

# --------------------------------------------------------------------------- hitungan & terbilang
_SATUAN = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]


def _terbilang(n: int) -> str:
    if n < 12:
        return _SATUAN[n]
    if n < 20:
        return _terbilang(n - 10) + " belas"
    if n < 100:
        return (_terbilang(n // 10) + " puluh " + _terbilang(n % 10)).strip()
    if n < 200:
        return ("seratus " + _terbilang(n - 100)).strip()
    if n < 1000:
        return (_terbilang(n // 100) + " ratus " + _terbilang(n % 100)).strip()
    if n < 2000:
        return ("seribu " + _terbilang(n - 1000)).strip()
    for size, name in ((10**12, "triliun"), (10**9, "miliar"), (10**6, "juta"), (10**3, "ribu")):
        if n >= size:
            return (_terbilang(n // size) + f" {name} " + _terbilang(n % size)).strip()
    return ""


def terbilang(value) -> str:
    n = int(Decimal(value or 0).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    text = "nol" if n == 0 else re.sub(r"\s+", " ", _terbilang(n))
    return text[0].upper() + text[1:] + " rupiah"


def tanggal_id(value) -> str:
    if not value:
        return "-"
    if isinstance(value, datetime):
        value = value.date()
    return f"{value.day} {BULAN[value.month - 1]} {value.year}"


def _dec(text, default="0"):
    text = str(text if text not in (None, "") else default).strip().replace(" ", "")
    if re.fullmatch(r"\d{1,3}(\.\d{3})+(,\d+)?", text):  # 19.300.000 / 1.000,50
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return Decimal(default)


def compute(items, include_tax=True, tax_rate=Decimal("11"), fee_percent=Decimal("3")):
    """Total, DPP, PPN, fee. Harga termasuk PPN → DPP = total ÷ (1 + tarif)."""
    total = sum((Decimal(i["jumlah"]) for i in items), Decimal("0"))
    rate = Decimal(tax_rate) / Decimal("100")
    if include_tax:
        dpp = (total / (1 + rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ppn = total - dpp
        gross = total
    else:
        dpp = total
        ppn = (total * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        gross = total + ppn
    fee = (gross * Decimal(fee_percent) / Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return {"total": gross, "dpp": dpp, "ppn": ppn, "fee": fee}


# --------------------------------------------------------------------------- data
def _load(cursor, flag_id):
    cursor.execute(
        """
        SELECT b.id, b.perusahaan_id, co.kode, co.nama_resmi, b.nomor, b.tanggal_dokumen, COALESCE(b.tanggal_kegiatan, ''),
               b.nama_klien, COALESCE(b.instansi, ''), COALESCE(b.alamat, ''), COALESCE(b.judul_penelitian, ''),
               b.harga_termasuk_ppn, b.tarif_ppn, b.persen_fee, b.total, b.dpp, b.ppn, b.fee,
               COALESCE(b.penanda_tangan, ''), COALESCE(b.jabatan_penanda_tangan, ''), b.status,
               b.fee_diterima_pada, COALESCE(b.catatan, '')
        FROM pinjam_bendera b JOIN perusahaan co ON co.id = b.perusahaan_id WHERE b.id = %s
        """,
        [flag_id],
    )
    row = cursor.fetchone()
    if not row:
        return None
    keys = ["id", "company_id", "company_code", "company_name", "number", "doc_date", "activity_date", "client",
            "institution", "address", "research_title", "include_tax", "tax_rate", "fee_percent", "total", "dpp",
            "ppn", "fee", "signer", "signer_title", "status", "fee_received_on", "notes"]
    record = dict(zip(keys, row))
    cursor.execute(
        """
        SELECT rincian, banyaknya, satuan, volume, satuan_volume, harga_satuan, jumlah
        FROM pinjam_bendera_item WHERE pinjam_bendera_id = %s ORDER BY urutan, id
        """,
        [flag_id],
    )
    record["items"] = [dict(zip(["rincian", "banyaknya", "satuan", "volume", "satuan_volume", "harga_satuan",
                                 "jumlah"], r)) for r in cursor.fetchall()]
    return record


def _companies(cursor):
    cursor.execute("SELECT id, kode, nama_resmi FROM perusahaan WHERE aktif ORDER BY kode DESC")
    return cursor.fetchall()


def _next_number(cursor, company_code):
    prefix = f"{company_code}-LPJ/{date.today():%y}/"
    cursor.execute("SELECT nomor FROM pinjam_bendera WHERE nomor LIKE %s", [prefix + "%"])
    seq = 0
    for (nomor,) in cursor.fetchall():
        tail = nomor[len(prefix):]
        if tail.isdigit():
            seq = max(seq, int(tail))
    return f"{prefix}{seq + 1:03d}"


def _save(cursor, data, items, user, flag_id=None):
    sums = compute(items, data["include_tax"], data["tax_rate"], data["fee_percent"])
    values = [data["company_id"], data["number"], data["doc_date"], data["activity_date"] or None, data["client"],
              data["institution"] or None, data["address"] or None, data["research_title"] or None,
              data["include_tax"], data["tax_rate"], data["fee_percent"], sums["total"], sums["dpp"], sums["ppn"],
              sums["fee"], data["signer"] or None, data["signer_title"] or None, data["notes"] or None]
    if flag_id:
        cursor.execute(
            """
            UPDATE pinjam_bendera SET perusahaan_id=%s, nomor=%s, tanggal_dokumen=%s, tanggal_kegiatan=%s,
                nama_klien=%s, instansi=%s, alamat=%s, judul_penelitian=%s, harga_termasuk_ppn=%s, tarif_ppn=%s,
                persen_fee=%s, total=%s, dpp=%s, ppn=%s, fee=%s, penanda_tangan=%s, jabatan_penanda_tangan=%s,
                catatan=%s, diubah_pada=now()
            WHERE id=%s
            """,
            values + [flag_id],
        )
        cursor.execute("DELETE FROM pinjam_bendera_item WHERE pinjam_bendera_id = %s", [flag_id])
    else:
        cursor.execute(
            """
            INSERT INTO pinjam_bendera (perusahaan_id, nomor, tanggal_dokumen, tanggal_kegiatan, nama_klien, instansi,
                alamat, judul_penelitian, harga_termasuk_ppn, tarif_ppn, persen_fee, total, dpp, ppn, fee,
                penanda_tangan, jabatan_penanda_tangan, catatan, dibuat_oleh)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
            """,
            values + [user],
        )
        flag_id = cursor.fetchone()[0]
    for order, item in enumerate(items, 1):
        cursor.execute(
            """
            INSERT INTO pinjam_bendera_item (pinjam_bendera_id, urutan, rincian, banyaknya, satuan, volume,
                satuan_volume, harga_satuan, jumlah)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [flag_id, order, item["rincian"][:300], item["banyaknya"], item["satuan"][:30] or "unit", item["volume"],
             item["satuan_volume"][:30] or "ls", item["harga_satuan"], item["jumlah"]],
        )
    return flag_id


def _items_from_post(post):
    items = []
    names = post.getlist("rincian")
    cols = {key: post.getlist(key) + [""] * len(names)
            for key in ("banyaknya", "satuan", "volume", "satuan_volume", "harga_satuan")}
    for idx, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        qty = _dec(cols["banyaknya"][idx], "1") or Decimal("1")
        vol = _dec(cols["volume"][idx], "1") or Decimal("1")
        price = _dec(cols["harga_satuan"][idx], "0")
        items.append({"rincian": name, "banyaknya": qty, "satuan": cols["satuan"][idx].strip() or "unit",
                      "volume": vol, "satuan_volume": cols["satuan_volume"][idx].strip() or "ls",
                      "harga_satuan": price, "jumlah": (qty * vol * price).quantize(Decimal("1"))})
    return items


# --------------------------------------------------------------------------- views
@business_permission_required("INVOICE", "LIHAT")
def flag_list(request):
    month = request.GET.get("bulan", "")
    status = request.GET.get("status", "")
    rows, totals, months = [], {"total": 0, "dpp": 0, "ppn": 0, "fee": 0, "fee_received": 0}, []
    database_error = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT b.id, co.kode, b.nomor, b.tanggal_dokumen, COALESCE(b.tanggal_kegiatan, ''), b.nama_klien,
                       COALESCE(b.judul_penelitian, ''), b.total, b.dpp, b.ppn, b.fee, b.persen_fee, b.status,
                       (SELECT string_agg(i.rincian, '; ' ORDER BY i.urutan) FROM pinjam_bendera_item i
                        WHERE i.pinjam_bendera_id = b.id), to_char(b.tanggal_dokumen, 'YYYY-MM')
                FROM pinjam_bendera b JOIN perusahaan co ON co.id = b.perusahaan_id
                WHERE (%s = '' OR to_char(b.tanggal_dokumen, 'YYYY-MM') = %s)
                  AND (%s = '' OR b.status = %s)
                ORDER BY b.tanggal_dokumen DESC, b.id DESC
                LIMIT 500
                """,
                [month, month, status, status],
            )
            for r in cursor.fetchall():
                rows.append({"id": r[0], "company": r[1], "number": r[2], "date": r[3], "activity": r[4],
                             "client": r[5], "title": r[6], "total": r[7], "dpp": r[8], "ppn": r[9], "fee": r[10],
                             "fee_percent": r[11], "status": r[12], "status_label": STATUS_LABELS.get(r[12], r[12]),
                             "items": r[13] or ""})
                if r[12] != "DIBATALKAN":
                    for key, value in (("total", r[7]), ("dpp", r[8]), ("ppn", r[9]), ("fee", r[10])):
                        totals[key] += value
                    if r[12] == "FEE_DITERIMA":
                        totals["fee_received"] += r[10]
            cursor.execute("SELECT DISTINCT to_char(tanggal_dokumen, 'YYYY-MM') FROM pinjam_bendera ORDER BY 1 DESC")
            months = [m for (m,) in cursor.fetchall()]
    except DatabaseError:
        database_error = "Tabel pinjam bendera belum ada. Tutup lalu jalankan ulang Seanav Operations."
    month_labels = [(m, f"{BULAN[int(m[5:]) - 1]} {m[:4]}") for m in months]
    return render(request, "portal/flag_list.html", {
        "rows": rows, "totals": totals, "month": month, "status": status, "months": month_labels,
        "statuses": STATUS_LABELS, "database_error": database_error,
    })


@business_permission_required("INVOICE", "BUAT")
def flag_form(request, flag_id=None):
    try:
        with connection.cursor() as cursor:
            companies = _companies(cursor)
            record = _load(cursor, flag_id) if flag_id else None
            if flag_id and not record:
                raise Http404("Data pinjam bendera tidak ditemukan.")
            default_company = next((c for c in companies if c[1] == "SNV"), companies[0] if companies else None)
            suggested = _next_number(cursor, default_company[1]) if default_company and not record else ""
    except DatabaseError:
        messages.error(request, "Tabel pinjam bendera belum ada. Tutup lalu jalankan ulang Seanav Operations.")
        return redirect("flag_list")
    errors = []
    if request.method == "POST":
        post = request.POST
        data = {
            "company_id": post.get("company_id"), "number": (post.get("number") or "").strip()[:80],
            "activity_date": (post.get("activity_date") or "").strip()[:120],
            "client": (post.get("client") or "").strip()[:200],
            "institution": (post.get("institution") or "").strip()[:200],
            "address": (post.get("address") or "").strip(),
            "research_title": (post.get("research_title") or "").strip(),
            "include_tax": post.get("include_tax") == "on",
            "tax_rate": _dec(post.get("tax_rate"), "11"), "fee_percent": _dec(post.get("fee_percent"), "3"),
            "signer": (post.get("signer") or "").strip()[:160],
            "signer_title": (post.get("signer_title") or "").strip()[:120],
            "notes": (post.get("notes") or "").strip(),
        }
        try:
            data["doc_date"] = datetime.strptime(post.get("doc_date") or "", "%Y-%m-%d").date()
        except ValueError:
            data["doc_date"] = date.today()
        items = _items_from_post(post)
        if not data["number"]:
            errors.append("Isi nomor dokumen.")
        if not data["client"]:
            errors.append("Isi nama klien / ketua peneliti.")
        if not items:
            errors.append("Tambahkan minimal satu rincian.")
        if not errors:
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    saved_id = _save(cursor, data, items, request.user.get_username(), flag_id)
                messages.success(request, "Data pinjam bendera disimpan. Unduh Invoice, Kwitansi, atau BAST dari daftar.")
                return redirect("flag_list")
            except IntegrityError:
                errors.append("Nomor dokumen sudah dipakai. Gunakan nomor lain.")
            except DatabaseError:
                logger.exception("Pinjam bendera gagal disimpan")
                errors.append("Data belum tersimpan. Periksa isian dan database.")
        record = {**(record or {}), **data, "items": items, "doc_date": data["doc_date"]}
    if not record:
        record = {"number": suggested, "doc_date": date.today(), "include_tax": True, "tax_rate": Decimal("11"),
                  "fee_percent": Decimal("3"), "company_id": default_company[0] if default_company else None,
                  "items": [], "signer": request.user.get_full_name() or "", "signer_title": "Direktur"}
    return render(request, "portal/flag_form.html", {
        "record": record, "companies": companies, "errors": errors, "flag_id": flag_id,
    })


@business_permission_required("INVOICE", "UBAH")
def flag_status(request, flag_id, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    updates = {
        "terbit": ("status = 'DOKUMEN_TERBIT'", "Ditandai dokumen sudah terbit."),
        "fee": ("status = 'FEE_DITERIMA', fee_diterima_pada = CURRENT_DATE", "Fee dicatat sudah diterima."),
        "batal": ("status = 'DIBATALKAN'", "Data dibatalkan (tidak dihitung di rekap)."),
        "draft": ("status = 'DRAFT', fee_diterima_pada = NULL", "Status dikembalikan ke Draft."),
    }
    if action == "hapus":
        try:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM pinjam_bendera WHERE id = %s AND status IN ('DRAFT', 'DIBATALKAN')", [flag_id])
                deleted = cursor.rowcount
            messages.success(request, "Data dihapus.") if deleted else messages.error(
                request, "Hanya data Draft atau Dibatalkan yang bisa dihapus.")
        except DatabaseError:
            messages.error(request, "Gagal menghapus data.")
        return redirect("flag_list")
    if action not in updates:
        raise Http404("Aksi tidak dikenal.")
    sql, text = updates[action]
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE pinjam_bendera SET {sql}, diubah_pada = now() WHERE id = %s", [flag_id])
        messages.success(request, text)
    except DatabaseError:
        messages.error(request, "Status belum tersimpan.")
    return redirect("flag_list")


# --------------------------------------------------------------------------- dokumen
def _qty_text(value):
    value = Decimal(value)
    return f"{value.normalize():f}" if value != value.to_integral() else f"{int(value)}"


def _doc_items(record):
    """Item untuk invoice. Harga termasuk PPN dipecah ke DPP agar Subtotal = DPP dan PPN terpisah."""
    items = record["items"]
    if not items:
        return []
    factor = Decimal("1")
    if record["include_tax"]:
        gross = sum(Decimal(i["jumlah"]) for i in items) or Decimal("1")
        factor = Decimal(record["dpp"]) / gross
    rows, running = [], Decimal("0")
    for idx, item in enumerate(items):
        amount = (Decimal(item["jumlah"]) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        if idx == len(items) - 1:
            amount = Decimal(record["dpp"]) - running
        running += amount
        multiplier = Decimal(item["banyaknya"]) * Decimal(item["volume"])
        details = []  # judul penelitian & tanggal kegiatan sudah tampil di kotak Project / period
        volume = Decimal(item["volume"])
        rows.append({"name": item["rincian"], "details": details,
                     "qty": item["banyaknya"], "unit": item["satuan"],
                     "days": None if volume == 1 else volume,
                     "duration_text": None if volume == 1 else f"{_qty_text(volume)} {item['satuan_volume']}",
                     "price": amount / multiplier if multiplier else amount})
    return rows


def _lpj_payload(record):
    signer_title = record["signer_title"] or ""
    if signer_title.strip().lower() in ("authorized signature", "authorized signatured"):
        signer_title = "Direktur"
    purpose = "; ".join(i["rincian"] for i in record["items"])
    if record["research_title"]:
        purpose += f", untuk kegiatan penelitian \u201c{record['research_title']}\u201d"
    if record["activity_date"]:
        purpose += f" ({record['activity_date']})"
    return {
        "brand": _brand_code(record["company_code"]), "number": record["number"],
        "invoice_no": f"INV/{record['number']}", "receipt_no": f"KW/{record['number']}",
        "bast_no": f"BAST/{record['number']}", "doc_date": record["doc_date"], "client": record["client"],
        "institution": record["institution"], "address_lines": [x for x in record["address"].splitlines() if x.strip()][:3],
        "research_title": record["research_title"], "activity_date": record["activity_date"], "items": record["items"],
        "include_tax": record["include_tax"], "tax_rate": record["tax_rate"], "total": record["total"],
        "dpp": record["dpp"], "ppn": record["ppn"], "terbilang": terbilang(record["total"]), "purpose": purpose,
        "signer": record["signer"], "signer_title": signer_title,
    }


def _flag_invoice(record):
    data = _lpj_payload(record)
    return data["brand"], build_lpj_invoice(data), data["invoice_no"]


def _flag_receipt(record):
    data = _lpj_payload(record)
    return data["brand"], build_lpj_receipt(data), data["receipt_no"]


def _flag_bast(record):
    data = _lpj_payload(record)
    return data["brand"], build_lpj_bast(data), data["bast_no"]


DOCS = {"invoice": ("INV", _flag_invoice), "kwitansi": ("KW", _flag_receipt), "bast": ("BAST", _flag_bast)}


@business_permission_required("INVOICE", "LIHAT")
def flag_document(request, flag_id, doc):
    if doc not in DOCS:
        raise Http404("Dokumen tidak dikenal.")
    try:
        with connection.cursor() as cursor:
            record = _load(cursor, flag_id)
    except DatabaseError:
        record = None
    if not record:
        raise Http404("Data pinjam bendera tidak ditemukan.")
    prefix, builder = DOCS[doc]
    _company, content, number = builder(record)
    names = [i["rincian"] for i in record["items"]]
    fname = file_name(prefix, record["number"], record["client"], None, names[:2])
    return _respond(request, content, number, "flag_list", fname)


# --------------------------------------------------------------------------- impor & ekspor Excel
HEADERS = ["No", "Tanggal", "Rincian", "Judul Penelitian", "Ketua Peneliti", "Banyaknya", "Satuan", "Volume",
           "Satuan", "Harga satuan", "Jumlah Biaya", "DPP", "PPN", "Fee"]


@business_permission_required("INVOICE", "BUAT")
def flag_import(request):
    """Impor dari Excel dengan format: No | Tanggal | Rincian | Judul Penelitian | Ketua Peneliti | Banyaknya |
    Satuan | Volume | Satuan | Harga satuan | Jumlah Biaya | ... Baris tanpa No = rincian tambahan baris di atasnya."""
    preview, errors = [], []
    try:
        with connection.cursor() as cursor:
            companies = _companies(cursor)
    except DatabaseError:
        messages.error(request, "Tabel pinjam bendera belum ada. Tutup lalu jalankan ulang Seanav Operations.")
        return redirect("flag_list")
    if request.method == "POST":
        upload = request.FILES.get("file")
        company_id = request.POST.get("company_id")
        fee_percent = _dec(request.POST.get("fee_percent"), "3")
        stored = request.session.get("flag_import")
        if upload and not upload.name.lower().endswith((".xlsx", ".xlsm")):
            errors.append("File harus berformat Excel (.xlsx).")
        elif not upload and not stored:
            errors.append("Pilih file Excel (.xlsx).")
        else:
            try:
                groups = []
                if upload:
                    from openpyxl import load_workbook
                    wb = load_workbook(io.BytesIO(upload.read()), data_only=True)
                    ws = wb.active
                    for row in ws.iter_rows(min_row=1, values_only=True):
                        row = list(row) + [None] * 14
                        if not any(v not in (None, "") for v in row[:11]) or not row[2]:
                            continue
                        if str(row[0] or "").strip().lower() == "no" or str(row[2]).strip().lower() == "rincian":
                            continue  # baris judul kolom
                        qty, vol, price = _dec(row[5], "1") or Decimal("1"), _dec(row[7], "1") or Decimal("1"), _dec(row[9], "0")
                        total = _dec(row[10], "0") or (qty * vol * price)
                        item = {"rincian": str(row[2]).strip(), "banyaknya": qty, "satuan": str(row[6] or "unit").strip(),
                                "volume": vol, "satuan_volume": str(row[8] or "ls").strip(), "harga_satuan": price,
                                "jumlah": total.quantize(Decimal("1"))}
                        if row[0] not in (None, "") or not groups:
                            groups.append({"activity_date": str(row[1] or "").strip(),
                                           "research_title": str(row[3] or "").strip(),
                                           "client": str(row[4] or "").strip(), "items": [item]})
                        else:
                            groups[-1]["items"].append(item)
                    request.session["flag_import"] = [
                        {**g, "items": [{k: str(v) for k, v in it.items()} for it in g["items"]]} for g in groups]
                else:
                    groups = [{**g, "items": [{**it, **{k: Decimal(it[k]) for k in ("banyaknya", "volume", "harga_satuan", "jumlah")}}
                                              for it in g["items"]]} for g in stored]
                if request.POST.get("confirm") == "1":
                    request.session.pop("flag_import", None)
                    created = 0
                    with transaction.atomic(), connection.cursor() as cursor:
                        code = next((c[1] for c in companies if str(c[0]) == str(company_id)), "SNV")
                        for g in groups:
                            if not g["client"]:
                                continue
                            data = {"company_id": company_id, "number": _next_number(cursor, code),
                                    "doc_date": date.today(), "activity_date": g["activity_date"], "client": g["client"],
                                    "institution": "", "address": "", "research_title": g["research_title"],
                                    "include_tax": True, "tax_rate": Decimal("11"), "fee_percent": fee_percent,
                                    "signer": request.user.get_full_name() or "", "signer_title": "Direktur",
                                    "notes": "Diimpor dari Excel"}
                            _save(cursor, data, g["items"], request.user.get_username())
                            created += 1
                    messages.success(request, f"{created} data pinjam bendera diimpor. Periksa nomor & tanggal dokumen, lalu unduh dokumennya.")
                    return redirect("flag_list")
                for g in groups:
                    sums = compute(g["items"], True, Decimal("11"), fee_percent)
                    preview.append({**g, **sums})
                if not preview:
                    errors.append("Tidak ada baris data yang terbaca. Pastikan baris pertama adalah judul kolom.")
            except Exception as exc:  # file rusak / format beda
                logger.exception("Impor pinjam bendera gagal")
                errors.append(f"File tidak bisa dibaca: {exc}")
    return render(request, "portal/flag_import.html", {
        "companies": companies, "preview": preview, "errors": errors,
        "company_id": request.POST.get("company_id", ""), "fee_percent": request.POST.get("fee_percent", "3"),
    })


@business_permission_required("INVOICE", "LIHAT")
def flag_export(request):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    month = request.GET.get("bulan", "")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT b.id, b.nomor, COALESCE(b.tanggal_kegiatan, to_char(b.tanggal_dokumen, 'DD-MM-YYYY')),
                   COALESCE(b.judul_penelitian, ''), b.nama_klien, b.harga_termasuk_ppn, b.tarif_ppn, b.persen_fee,
                   b.status, co.kode
            FROM pinjam_bendera b JOIN perusahaan co ON co.id = b.perusahaan_id
            WHERE b.status <> 'DIBATALKAN' AND (%s = '' OR to_char(b.tanggal_dokumen, 'YYYY-MM') = %s)
            ORDER BY b.tanggal_dokumen, b.id
            """,
            [month, month],
        )
        heads = cursor.fetchall()
        wb = Workbook()
        ws = wb.active
        ws.title = "Pinjam Bendera"
        ws.append(HEADERS[:13] + ["Fee", "Perusahaan", "No. Dokumen", "Status"])
        for cell in ws[1]:
            cell.font = Font(name="Arial", bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="0B2A4A")
            cell.alignment = Alignment(wrap_text=True, vertical="center")
        no = 0
        for head in heads:
            cursor.execute(
                """SELECT rincian, banyaknya, satuan, volume, satuan_volume, harga_satuan, jumlah
                   FROM pinjam_bendera_item WHERE pinjam_bendera_id = %s ORDER BY urutan, id""", [head[0]])
            no += 1
            for idx, it in enumerate(cursor.fetchall()):
                r = ws.max_row + 1
                rate = float(head[6]) / 100
                ws.append([no if idx == 0 else None, head[2] if idx == 0 else None, it[0],
                           head[3] if idx == 0 else None, head[4] if idx == 0 else None,
                           float(it[1]), it[2], float(it[3]), it[4], float(it[5]), float(it[6]),
                           f"=K{r}/(1+{rate})" if head[5] else f"=K{r}",
                           f"=K{r}-L{r}" if head[5] else f"=K{r}*{rate}",
                           f"=(K{r}{'' if head[5] else f'*(1+{rate})'})*{float(head[7]) / 100}",
                           head[9] if idx == 0 else None, head[1] if idx == 0 else None,
                           STATUS_LABELS.get(head[8], head[8]) if idx == 0 else None])
        last = ws.max_row
        ws.append([None, None, "TOTAL", None, None, None, None, None, None, None,
                   f"=SUM(K2:K{last})", f"=SUM(L2:L{last})", f"=SUM(M2:M{last})", f"=SUM(N2:N{last})"])
        for cell in ws[ws.max_row]:
            cell.font = Font(name="Arial", bold=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if cell.font is None or not cell.font.bold:
                    cell.font = Font(name="Arial", size=10)
                if cell.column >= 10 and cell.column <= 14:
                    cell.number_format = "#,##0"
        widths = [5, 18, 42, 48, 30, 10, 8, 8, 8, 14, 14, 14, 14, 12, 10, 20, 14]
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[chr(64 + i)].width = w
        ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    label = month or date.today().strftime("%Y-%m")
    response = HttpResponse(buf.getvalue(),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="Rekap Pinjam Bendera {label}.xlsx"'
    return response
