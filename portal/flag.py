"""Pinjam Bendera — dokumen atas nama perusahaan untuk kebutuhan pihak lain (penelitian, perbaikan, pengadaan, dll).

Data berdiri sendiri: tidak membuat quotation, invoice modul, rental, atau mengubah status alat.
Kategori pekerjaan bebas. Jenis klien:
  * UMUM        — bruto termasuk PPN, tanpa potongan (neto = bruto).
  * PEMERINTAH  — instansi pemungut: PPN dipungut bendahara, PPh 22 atas barang, PPh 23 atas jasa,
                  sehingga yang masuk ke rekening = neto.
Fee perusahaan = persen × neto (catatan internal, tidak tercetak). Dana diteruskan = neto − fee.
"""
import io
import json
import logging
import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django.contrib import messages
from django.db import DatabaseError, IntegrityError, connection, transaction
from django.http import Http404, HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render

from .doc_builder import amount_in_words, fmt_idr
from .lpj_builder import build_lpj_bast, build_lpj_invoice, build_lpj_receipt, en_item, en_text
from .lpj_gov_builder import build_gov_bast, build_gov_receipt, rp_id
from .document_views import _brand_code, _respond, file_name
from .views import business_permission_required

logger = logging.getLogger(__name__)

STATUS_LABELS = {"DRAFT": "Draft", "DOKUMEN_TERBIT": "Dokumen terbit", "DANA_MASUK": "Dana masuk",
                 "DITERUSKAN": "Diteruskan ke peminjam", "FEE_DITERIMA": "Fee diterima", "DIBATALKAN": "Dibatalkan"}
CLIENT_TYPES = {"UMUM": "Umum", "PEMERINTAH": "Instansi pemerintah (pemungut pajak)"}
DEFAULT_CATEGORIES = ["Penelitian", "Perbaikan / Maintenance", "Pengadaan Barang", "Jasa Survey", "Sewa Alat",
                      "Lainnya"]
# Data khusus instansi pemerintah (disimpan di kolom JSON data_pemerintah)
GOV_FIELDS = [
    ("kementerian", "Kementerian / lembaga", "Kementerian Pertahanan"),
    ("kode_satker", "Kode satker / NOMINKU", "30105"),
    ("tahun_anggaran", "Tahun anggaran", "2026"),
    ("kode_dipa", "Kode program & anggaran (DIPA)", "DIPA-012.23.AF.6515.CCF.002.003.523121"),
    ("jenis_pengeluaran", "Jenis pengeluaran", "Belanja pemeliharaan peralatan dan mesin"),
    ("dasar_nomor", "Dasar pekerjaan (Sprinlak / SPK) — nomor", "Sprinlak/RM/50/X/2026"),
    ("dasar_tanggal", "Tanggal dasar pekerjaan", "22 Oktober 2026"),
    ("spp_nomor", "Nomor SPP", "SPP/…/…/2026"),
    ("jabatan_klien", "Jabatan penerima pekerjaan (untuk BAST)", "Pejabat Pembuat Komitmen"),
    ("bendahara_nama", "Nama bendahara", "Indra Guntara, S.T.Han"),
    ("bendahara_pangkat", "Pangkat / NRP bendahara", "Mayor Laut (S) NRP 20770/P"),
    ("bendahara_jabatan", "Jabatan bendahara", "Bendahara Pengeluaran Pushidrosal"),
    ("bendahara_npwp", "NPWP bendahara", "00.073.348.5.044.000"),
]
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


def _r(value):
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def compute(items, include_tax=True, tax_rate=Decimal("11"), fee_percent=Decimal("3"), gov=False,
            ppn_collected=True, pph22_rate=Decimal("1.5"), pph23_rate=Decimal("2")):
    """Bruto, DPP, PPN, PPh 22/23, neto, fee, dana diteruskan.

    Harga termasuk PPN → DPP = bruto ÷ (1 + tarif). Untuk klien pemerintah DPP dibagi ke barang & jasa
    sebanding nilai item: PPh 22 = tarif × DPP barang, PPh 23 = tarif × DPP jasa.
    """
    total = sum((Decimal(i["jumlah"]) for i in items), Decimal("0"))
    rate = Decimal(tax_rate) / Decimal("100")
    if include_tax:
        dpp = _r(total / (1 + rate))
        ppn = total - dpp
        gross = total
    else:
        dpp = total
        ppn = _r(total * rate)
        gross = total + ppn
    dpp_goods = dpp_services = Decimal("0")
    pph22 = pph23 = Decimal("0")
    if gov:
        goods = sum((Decimal(i["jumlah"]) for i in items if i.get("jenis") == "BARANG"), Decimal("0"))
        dpp_goods = _r(dpp * goods / total) if total else Decimal("0")
        dpp_services = dpp - dpp_goods
        pph22 = _r(dpp_goods * Decimal(pph22_rate) / 100)
        pph23 = _r(dpp_services * Decimal(pph23_rate) / 100)
    collected = ppn if gov and ppn_collected else Decimal("0")
    withheld = collected + pph22 + pph23
    neto = gross - withheld
    fee = _r(neto * Decimal(fee_percent) / Decimal("100"))
    return {"total": gross, "dpp": dpp, "ppn": ppn, "dpp_goods": dpp_goods, "dpp_services": dpp_services,
            "pph22": pph22, "pph23": pph23, "ppn_collected": collected, "withheld": withheld, "neto": neto,
            "fee": fee, "forward": neto - fee}


def record_sums(record):
    return compute(record["items"], record["include_tax"], record["tax_rate"], record["fee_percent"],
                   record.get("client_type") == "PEMERINTAH", record.get("ppn_collected", True),
                   record.get("pph22_rate", Decimal("1.5")), record.get("pph23_rate", Decimal("2")))


# --------------------------------------------------------------------------- data
def _load(cursor, flag_id):
    cursor.execute(
        """
        SELECT b.id, b.perusahaan_id, co.kode, co.nama_resmi, b.nomor, b.tanggal_dokumen, COALESCE(b.tanggal_kegiatan, ''),
               b.nama_klien, COALESCE(b.instansi, ''), COALESCE(b.alamat, ''), COALESCE(b.judul_penelitian, ''),
               b.harga_termasuk_ppn, b.tarif_ppn, b.persen_fee, b.total, b.dpp, b.ppn, b.fee,
               COALESCE(b.penanda_tangan, ''), COALESCE(b.jabatan_penanda_tangan, ''), b.status,
               b.fee_diterima_pada, COALESCE(b.catatan, ''), b.kategori, b.jenis_klien, b.ppn_dipungut,
               b.tarif_pph22, b.tarif_pph23, b.pph22, b.pph23, COALESCE(b.neto, b.total), b.data_pemerintah,
               b.dana_masuk_pada, b.diteruskan_pada, b.bupot_diterima
        FROM pinjam_bendera b JOIN perusahaan co ON co.id = b.perusahaan_id WHERE b.id = %s
        """,
        [flag_id],
    )
    row = cursor.fetchone()
    if not row:
        return None
    keys = ["id", "company_id", "company_code", "company_name", "number", "doc_date", "activity_date", "client",
            "institution", "address", "research_title", "include_tax", "tax_rate", "fee_percent", "total", "dpp",
            "ppn", "fee", "signer", "signer_title", "status", "fee_received_on", "notes", "category", "client_type",
            "ppn_collected", "pph22_rate", "pph23_rate", "pph22", "pph23", "neto", "gov", "funds_in_on",
            "forwarded_on", "bupot_received"]
    record = dict(zip(keys, row))
    record["gov"] = _json(record["gov"])
    cursor.execute(
        """
        SELECT rincian, banyaknya, satuan, volume, satuan_volume, harga_satuan, jumlah, jenis
        FROM pinjam_bendera_item WHERE pinjam_bendera_id = %s ORDER BY urutan, id
        """,
        [flag_id],
    )
    record["items"] = [dict(zip(["rincian", "banyaknya", "satuan", "volume", "satuan_volume", "harga_satuan",
                                 "jumlah", "jenis"], r)) for r in cursor.fetchall()]
    record["sums"] = record_sums(record)
    return record


def _categories(cursor):
    cursor.execute("SELECT DISTINCT kategori FROM pinjam_bendera WHERE kategori <> '' ORDER BY 1")
    found = [c for (c,) in cursor.fetchall()]
    return DEFAULT_CATEGORIES + [c for c in found if c not in DEFAULT_CATEGORIES]


def _json(value):
    if isinstance(value, (bytes, str)):
        try:
            value = json.loads(value)
        except ValueError:
            value = {}
    return value if isinstance(value, dict) else {}


def _companies(cursor):
    cursor.execute("SELECT id, kode, nama_resmi FROM perusahaan WHERE aktif ORDER BY kode DESC")
    return cursor.fetchall()


def _next_number(cursor, company_code):
    """Nomor dasar: {KODE}-{yy}{urut 3 digit}, mis. SNV-26034. Dokumen: INV/SNV-26034/VIII/2026."""
    prefix = f"{company_code}-{date.today():%y}"
    cursor.execute("SELECT nomor FROM pinjam_bendera WHERE nomor LIKE %s", [prefix + "%"])
    seq = 0
    for (nomor,) in cursor.fetchall():
        tail = base_number(nomor)[len(prefix):]
        if tail.isdigit():
            seq = max(seq, int(tail))
    return f"{prefix}{seq + 1:03d}"


ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X", "XI", "XII"]


def base_number(number):
    """'INV/SNV-26034/VIII/2026' atau 'SNV-26034' -> 'SNV-26034'."""
    number = re.sub(r"^\s*(INV|RCPT|KW|BAST)\s*/\s*", "", (number or "").strip(), flags=re.I)
    return re.sub(r"/\s*[IVXLC]+\s*/\s*\d{4}\s*$", "", number)


def doc_number(prefix, number, doc_date):
    doc_date = doc_date or date.today()
    return f"{prefix}/{base_number(number)}/{ROMAN[doc_date.month - 1]}/{doc_date.year}"


def _save(cursor, data, items, user, flag_id=None):
    gov = data.get("client_type") == "PEMERINTAH"
    sums = compute(items, data["include_tax"], data["tax_rate"], data["fee_percent"], gov,
                   data.get("ppn_collected", True), data.get("pph22_rate", Decimal("1.5")),
                   data.get("pph23_rate", Decimal("2")))
    values = [data["company_id"], data["number"], data["doc_date"], data["activity_date"] or None, data["client"],
              data["institution"] or None, data["address"] or None, data["research_title"] or None,
              data["include_tax"], data["tax_rate"], data["fee_percent"], sums["total"], sums["dpp"], sums["ppn"],
              sums["fee"], data["signer"] or None, data["signer_title"] or None, data["notes"] or None,
              (data.get("category") or "Penelitian")[:60], "PEMERINTAH" if gov else "UMUM",
              bool(gov and data.get("ppn_collected", True)), data.get("pph22_rate", Decimal("1.5")),
              data.get("pph23_rate", Decimal("2")), sums["pph22"], sums["pph23"], sums["neto"],
              json.dumps(data.get("gov") or {} if gov else {})]
    if flag_id:
        cursor.execute(
            """
            UPDATE pinjam_bendera SET perusahaan_id=%s, nomor=%s, tanggal_dokumen=%s, tanggal_kegiatan=%s,
                nama_klien=%s, instansi=%s, alamat=%s, judul_penelitian=%s, harga_termasuk_ppn=%s, tarif_ppn=%s,
                persen_fee=%s, total=%s, dpp=%s, ppn=%s, fee=%s, penanda_tangan=%s, jabatan_penanda_tangan=%s,
                catatan=%s, kategori=%s, jenis_klien=%s, ppn_dipungut=%s, tarif_pph22=%s, tarif_pph23=%s,
                pph22=%s, pph23=%s, neto=%s, data_pemerintah=%s::jsonb, diubah_pada=now()
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
                penanda_tangan, jabatan_penanda_tangan, catatan, kategori, jenis_klien, ppn_dipungut, tarif_pph22,
                tarif_pph23, pph22, pph23, neto, data_pemerintah, dibuat_oleh)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s::jsonb, %s) RETURNING id
            """,
            values + [user],
        )
        flag_id = cursor.fetchone()[0]
    for order, item in enumerate(items, 1):
        cursor.execute(
            """
            INSERT INTO pinjam_bendera_item (pinjam_bendera_id, urutan, rincian, banyaknya, satuan, volume,
                satuan_volume, harga_satuan, jumlah, jenis)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [flag_id, order, item["rincian"][:300], item["banyaknya"], item["satuan"][:30] or "unit", item["volume"],
             item["satuan_volume"][:30] or "ls", item["harga_satuan"], item["jumlah"],
             "BARANG" if item.get("jenis") == "BARANG" else "JASA"],
        )
    return flag_id


def _items_from_post(post):
    items = []
    names = post.getlist("rincian")
    cols = {key: post.getlist(key) + [""] * len(names)
            for key in ("banyaknya", "satuan", "volume", "satuan_volume", "harga_satuan", "jenis")}
    for idx, name in enumerate(names):
        name = name.strip()
        if not name:
            continue
        qty = _dec(cols["banyaknya"][idx], "1") or Decimal("1")
        vol = _dec(cols["volume"][idx], "1") or Decimal("1")
        price = _dec(cols["harga_satuan"][idx], "0")
        items.append({"rincian": name, "banyaknya": qty, "satuan": cols["satuan"][idx].strip() or "unit",
                      "volume": vol, "satuan_volume": cols["satuan_volume"][idx].strip() or "ls",
                      "harga_satuan": price, "jumlah": (qty * vol * price).quantize(Decimal("1")),
                      "jenis": "BARANG" if cols["jenis"][idx] == "BARANG" else "JASA"})
    return items


# --------------------------------------------------------------------------- views
@business_permission_required("INVOICE", "LIHAT")
def flag_list(request):
    month = request.GET.get("bulan", "")
    status = request.GET.get("status", "")
    category = request.GET.get("kategori", "")
    client_type = request.GET.get("klien", "")
    rows, months, categories = [], [], []
    totals = {"total": 0, "dpp": 0, "ppn": 0, "withheld": 0, "neto": 0, "fee": 0, "fee_received": 0, "pending": 0}
    database_error = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT b.id, co.kode, b.nomor, b.tanggal_dokumen, COALESCE(b.tanggal_kegiatan, ''), b.nama_klien,
                       COALESCE(b.judul_penelitian, ''), b.total, b.dpp, b.ppn, b.fee, b.persen_fee, b.status,
                       (SELECT string_agg(i.rincian, '; ' ORDER BY i.urutan) FROM pinjam_bendera_item i
                        WHERE i.pinjam_bendera_id = b.id), to_char(b.tanggal_dokumen, 'YYYY-MM'),
                       b.kategori, b.jenis_klien, COALESCE(b.neto, b.total), b.pph22, b.pph23, b.ppn_dipungut,
                       b.bupot_diterima, COALESCE(b.instansi, ''), b.dana_masuk_pada, b.diteruskan_pada
                FROM pinjam_bendera b JOIN perusahaan co ON co.id = b.perusahaan_id
                WHERE (%s = '' OR to_char(b.tanggal_dokumen, 'YYYY-MM') = %s)
                  AND (%s = '' OR b.status = %s)
                  AND (%s = '' OR b.kategori = %s)
                  AND (%s = '' OR b.jenis_klien = %s)
                ORDER BY b.tanggal_dokumen DESC, b.id DESC
                LIMIT 500
                """,
                [month, month, status, status, category, category, client_type, client_type],
            )
            for r in cursor.fetchall():
                withheld = (r[9] if r[20] else 0) + r[18] + r[19]
                rows.append({"id": r[0], "company": r[1], "number": r[2], "date": r[3], "activity": r[4],
                             "client": r[5], "title": r[6], "total": r[7], "dpp": r[8], "ppn": r[9], "fee": r[10],
                             "fee_percent": r[11], "status": r[12], "status_label": STATUS_LABELS.get(r[12], r[12]),
                             "items": r[13] or "", "category": r[15], "gov": r[16] == "PEMERINTAH", "neto": r[17],
                             "pph22": r[18], "pph23": r[19], "withheld": withheld, "bupot": r[21],
                             "institution": r[22], "funds_in_on": r[23], "forwarded_on": r[24],
                             "forward": r[17] - r[10]})
                if r[12] != "DIBATALKAN":
                    for key, value in (("total", r[7]), ("dpp", r[8]), ("ppn", r[9]), ("fee", r[10]),
                                       ("neto", r[17]), ("withheld", withheld)):
                        totals[key] += value
                    if r[12] in ("FEE_DITERIMA", "DITERUSKAN"):
                        totals["fee_received"] += r[10]
                    if r[12] == "DANA_MASUK":
                        totals["pending"] += r[17] - r[10]
            cursor.execute("SELECT DISTINCT to_char(tanggal_dokumen, 'YYYY-MM') FROM pinjam_bendera ORDER BY 1 DESC")
            months = [m for (m,) in cursor.fetchall()]
            categories = _categories(cursor)
    except DatabaseError:
        database_error = "Tabel pinjam bendera belum ada. Tutup lalu jalankan ulang Seanav Operations."
    month_labels = [(m, f"{BULAN[int(m[5:]) - 1]} {m[:4]}") for m in months]
    return render(request, "portal/flag_list.html", {
        "rows": rows, "totals": totals, "month": month, "status": status, "months": month_labels,
        "statuses": STATUS_LABELS, "database_error": database_error, "category": category,
        "categories": categories, "client_type": client_type, "client_types": CLIENT_TYPES,
    })


@business_permission_required("INVOICE", "BUAT")
def flag_form(request, flag_id=None):
    try:
        with connection.cursor() as cursor:
            companies = _companies(cursor)
            categories = _categories(cursor)
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
            "company_id": post.get("company_id"), "number": base_number(post.get("number") or "")[:80],
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
            "category": (post.get("category") or "").strip()[:60] or "Lainnya",
            "client_type": "PEMERINTAH" if post.get("client_type") == "PEMERINTAH" else "UMUM",
            "ppn_collected": post.get("ppn_collected") == "on",
            "pph22_rate": _dec(post.get("pph22_rate"), "1.5"), "pph23_rate": _dec(post.get("pph23_rate"), "2"),
            "gov": {key: (post.get(f"gov_{key}") or "").strip()[:200] for key, _l, _p in GOV_FIELDS},
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
        if data["client_type"] == "PEMERINTAH" and not data["institution"]:
            errors.append("Isi nama instansi / satker untuk klien pemerintah.")
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
        record["sums"] = record_sums(record)
    if not record:
        record = {"number": suggested, "doc_date": date.today(), "include_tax": True, "tax_rate": Decimal("11"),
                  "fee_percent": Decimal("3"), "company_id": default_company[0] if default_company else None,
                  "items": [], "signer": request.user.get_full_name() or "", "signer_title": "Direktur",
                  "category": "Penelitian", "client_type": "UMUM", "ppn_collected": True,
                  "pph22_rate": Decimal("1.5"), "pph23_rate": Decimal("2"), "gov": {}}
    gov_values = record.get("gov") or {}
    gov_fields = [{"key": k, "label": label, "placeholder": ph, "value": gov_values.get(k, "")}
                  for k, label, ph in GOV_FIELDS]
    return render(request, "portal/flag_form.html", {
        "record": record, "companies": companies, "errors": errors, "flag_id": flag_id,
        "categories": categories, "client_types": CLIENT_TYPES, "gov_fields": gov_fields,
    })


def _back(request, default="flag_list"):
    target = request.POST.get("next") or ""
    return redirect(target) if target.startswith("/pinjam-bendera/") else redirect(default)


@business_permission_required("INVOICE", "UBAH")
def flag_status(request, flag_id, action):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    updates = {
        "terbit": ("status = 'DOKUMEN_TERBIT'", "Ditandai dokumen sudah terbit."),
        "dana": ("status = 'DANA_MASUK', dana_masuk_pada = CURRENT_DATE",
                 "Dana dari klien dicatat sudah masuk rekening."),
        "teruskan": ("status = 'DITERUSKAN', diteruskan_pada = CURRENT_DATE, fee_diterima_pada = CURRENT_DATE",
                     "Dana dicatat sudah diteruskan ke peminjam (fee perusahaan tertahan)."),
        "fee": ("status = 'FEE_DITERIMA', fee_diterima_pada = CURRENT_DATE", "Fee dicatat sudah diterima."),
        "bupot": ("bupot_diterima = NOT bupot_diterima", "Status bukti potong diperbarui."),
        "batal": ("status = 'DIBATALKAN'", "Data dibatalkan (tidak dihitung di rekap)."),
        "draft": ("status = 'DRAFT', fee_diterima_pada = NULL, dana_masuk_pada = NULL, diteruskan_pada = NULL",
                  "Status dikembalikan ke Draft."),
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
        return _back(request)
    if action not in updates:
        raise Http404("Aksi tidak dikenal.")
    sql, text = updates[action]
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE pinjam_bendera SET {sql}, diubah_pada = now() WHERE id = %s", [flag_id])
        messages.success(request, text)
    except DatabaseError:
        messages.error(request, "Status belum tersimpan.")
    return _back(request)


# --------------------------------------------------------------------------- rekap pajak (klien pemerintah)
def _tax_rows(cursor, month):
    cursor.execute(
        """
        SELECT b.id, co.kode, b.nomor, b.tanggal_dokumen, b.nama_klien, COALESCE(b.instansi, ''), b.kategori,
               b.total, b.dpp, b.ppn, b.ppn_dipungut, b.pph22, b.pph23, COALESCE(b.neto, b.total), b.bupot_diterima,
               b.status, b.data_pemerintah
        FROM pinjam_bendera b JOIN perusahaan co ON co.id = b.perusahaan_id
        WHERE b.jenis_klien = 'PEMERINTAH' AND b.status <> 'DIBATALKAN'
          AND (%s = '' OR to_char(b.tanggal_dokumen, 'YYYY-MM') = %s)
        ORDER BY b.tanggal_dokumen, b.id
        """,
        [month, month],
    )
    rows = []
    for r in cursor.fetchall():
        gov = _json(r[16])
        rows.append({"id": r[0], "company": r[1], "number": r[2], "date": r[3], "client": r[4], "institution": r[5],
                     "category": r[6], "total": r[7], "dpp": r[8], "ppn": r[9],
                     "ppn_collected": r[9] if r[10] else Decimal("0"), "pph22": r[11], "pph23": r[12],
                     "neto": r[13], "bupot": r[14], "status": r[15], "status_label": STATUS_LABELS.get(r[15], r[15]),
                     "spp": gov.get("spp_nomor", ""), "basis": gov.get("dasar_nomor", ""),
                     "doc_no": doc_number("INV", r[2], r[3])})
    return rows


@business_permission_required("INVOICE", "LIHAT")
def flag_tax(request):
    month = request.GET.get("bulan", "")
    rows, months = [], []
    try:
        with connection.cursor() as cursor:
            rows = _tax_rows(cursor, month)
            cursor.execute("""SELECT DISTINCT to_char(tanggal_dokumen, 'YYYY-MM') FROM pinjam_bendera
                              WHERE jenis_klien = 'PEMERINTAH' ORDER BY 1 DESC""")
            months = [m for (m,) in cursor.fetchall()]
    except DatabaseError:
        messages.error(request, "Tabel pinjam bendera belum siap. Tutup lalu jalankan ulang Seanav Operations.")
    keys = ("total", "dpp", "ppn_collected", "pph22", "pph23", "neto")
    totals = {k: sum((r[k] for r in rows), Decimal("0")) for k in keys}
    totals["pph"] = totals["pph22"] + totals["pph23"]
    totals["bupot_missing"] = sum(1 for r in rows if not r["bupot"] and (r["pph22"] or r["pph23"]))
    if request.GET.get("format") == "xlsx":
        return _tax_export(rows, month)
    return render(request, "portal/flag_tax.html", {
        "rows": rows, "totals": totals, "month": month,
        "months": [(m, f"{BULAN[int(m[5:]) - 1]} {m[:4]}") for m in months],
    })


def _tax_export(rows, month):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Rekap Pajak"
    head = ["No", "Tanggal", "No. Invoice", "Perusahaan", "Instansi / satker", "Klien", "Kategori", "Dasar / Sprinlak",
            "No. SPP", "Bruto", "DPP", "PPN dipungut", "PPh 22", "PPh 23", "Neto", "Bukti potong", "Status"]
    ws.append(head)
    for cell in ws[1]:
        cell.font = Font(name="Arial", bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="0B2A4A")
    for n, r in enumerate(rows, 1):
        x = ws.max_row + 1
        ws.append([n, r["date"], r["doc_no"], r["company"], r["institution"], r["client"], r["category"], r["basis"],
                   r["spp"], float(r["total"]), float(r["dpp"]), float(r["ppn_collected"]), float(r["pph22"]),
                   float(r["pph23"]), f"=J{x}-L{x}-M{x}-N{x}", "Diterima" if r["bupot"] else "Belum",
                   r["status_label"]])
        ws.cell(x, 2).number_format = "DD-MM-YYYY"
    last = ws.max_row
    ws.append([None, None, "TOTAL"] + [None] * 6 + [f"=SUM({c}2:{c}{last})" for c in "JKLMNO"])
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = Font(name="Arial", size=10, bold=cell.row == ws.max_row)
            if 10 <= cell.column <= 15:
                cell.number_format = "#,##0"
    for i, w in enumerate([5, 12, 26, 10, 26, 26, 20, 26, 18, 14, 14, 14, 12, 12, 14, 12, 18], 1):
        ws.column_dimensions[chr(64 + i)].width = w
    ws.freeze_panes = "A2"
    buf = io.BytesIO()
    wb.save(buf)
    label = month or date.today().strftime("%Y-%m")
    response = HttpResponse(buf.getvalue(),
                            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="Rekap Pajak Pinjam Bendera {label}.xlsx"'
    return response


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


CATEGORY_EN = {"penelitian": "Research", "perbaikan / maintenance": "Repair and maintenance",
               "perbaikan": "Repair and maintenance", "maintenance": "Repair and maintenance",
               "pengadaan barang": "Procurement of goods", "jasa survey": "Survey services",
               "sewa alat": "Equipment rental", "lainnya": "Services"}


def _subject_en(record):
    title = record["research_title"]
    category = (record.get("category") or "Penelitian").strip()
    if category.lower() == "penelitian":
        return f"Equipment rental and services for the research project \u201c{title}\u201d" if title else ""
    label = CATEGORY_EN.get(category.lower(), en_text(category))
    return f"{label} \u2014 \u201c{title}\u201d" if title else label


def _purpose_id(record):
    g = record.get("gov") or {}
    work = record["research_title"] or "; ".join(i["rincian"] for i in record["items"])
    text = f"Pembayaran {work}"
    if g.get("dasar_nomor"):
        text += f" berdasarkan {g['dasar_nomor']}"
        if g.get("dasar_tanggal"):
            text += f" tanggal {g['dasar_tanggal']}"
    if g.get("spp_nomor"):
        text += f", SPP nomor {g['spp_nomor']}"
    text += "."
    if record["ppn"]:
        text += (f" Harga sudah termasuk PPN {_qty_text(record['tax_rate'])}% sebesar {rp_id(record['ppn'])} "
                 f"({terbilang(record['ppn'])}).")
    return text


def _lpj_payload(record):
    signer_title = record["signer_title"] or ""
    if signer_title.strip().lower() in ("authorized signature", "authorized signatured"):
        signer_title = "Director"
    sums = record.get("sums") or record_sums(record)
    research = (record.get("category") or "Penelitian").strip().lower() == "penelitian"
    project = "the research project" if research else "the project"
    purpose = "; ".join(en_item(i["rincian"]) for i in record["items"])
    if record["research_title"]:
        purpose += f" for {project} \u201c{record['research_title']}\u201d"
    if record["activity_date"]:
        purpose += f" ({en_text(record['activity_date'])})"
    gov = record.get("client_type") == "PEMERINTAH"
    withholding = []
    if gov and sums["withheld"]:
        if sums["ppn_collected"]:
            withholding.append(("Less: VAT collected by government treasurer", sums["ppn_collected"]))
        if sums["pph22"]:
            withholding.append((f"Less: Income tax art. 22 ({_qty_text(record['pph22_rate'])}% of goods)",
                                sums["pph22"]))
        if sums["pph23"]:
            withholding.append((f"Less: Income tax art. 23 ({_qty_text(record['pph23_rate'])}% of services)",
                                sums["pph23"]))
    return {
        "brand": _brand_code(record["company_code"]), "number": base_number(record["number"]),
        "invoice_no": doc_number("INV", record["number"], record["doc_date"]),
        "receipt_no": doc_number("RCPT", record["number"], record["doc_date"]),
        "kw_no": doc_number("KW", record["number"], record["doc_date"]),
        "bast_no": doc_number("BAST", record["number"], record["doc_date"]), "doc_date": record["doc_date"], "client": record["client"],
        "institution": record["institution"], "address_lines": [x for x in record["address"].splitlines() if x.strip()][:3],
        "research_title": record["research_title"], "activity_date": record["activity_date"], "items": record["items"],
        "include_tax": record["include_tax"], "tax_rate": record["tax_rate"], "total": record["total"],
        "dpp": record["dpp"], "ppn": record["ppn"], "terbilang": amount_in_words(record["total"]), "purpose": purpose,
        "signer": record["signer"], "signer_title": signer_title, "subject": _subject_en(record),
        "project": project, "withholding": withholding, "neto": sums["neto"], "gov": record.get("gov") or {},
        "sums": sums, "terbilang_id": terbilang(record["total"]), "purpose_id": _purpose_id(record),
        "pph22_rate": record.get("pph22_rate", Decimal("1.5")), "pph23_rate": record.get("pph23_rate", Decimal("2")),
        "is_gov": gov,
    }


def _flag_invoice(record):
    data = _lpj_payload(record)
    return data["brand"], build_lpj_invoice(data), data["invoice_no"]


def _flag_receipt(record):
    data = _lpj_payload(record)
    if data["is_gov"]:
        return data["brand"], build_gov_receipt(data), data["kw_no"]
    return data["brand"], build_lpj_receipt(data), data["receipt_no"]


def _flag_bast(record):
    data = _lpj_payload(record)
    return data["brand"], (build_gov_bast if data["is_gov"] else build_lpj_bast)(data), data["bast_no"]


DOCS = {"invoice": ("INV", _flag_invoice), "kwitansi": ("RCPT", _flag_receipt), "bast": ("BAST", _flag_bast)}


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
    if doc == "kwitansi" and record.get("client_type") == "PEMERINTAH":
        prefix = "KW"
    _company, content, number = builder(record)
    names = [en_item(i["rincian"]) for i in record["items"]]
    fname = file_name(prefix, base_number(record["number"]), record["client"], None, names[:2])
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
                   b.status, co.kode, b.kategori, b.jenis_klien, COALESCE(b.neto, b.total), b.fee
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
        ws.append(HEADERS[:13] + ["Fee", "Perusahaan", "No. Dokumen", "Status", "Kategori", "Jenis klien", "Neto"])
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
                           (f"=(K{r}{'' if head[5] else f'*(1+{rate})'})*{float(head[7]) / 100}"
                            if head[11] != "PEMERINTAH" else (float(head[13]) if idx == 0 else None)),
                           head[9] if idx == 0 else None, head[1] if idx == 0 else None,
                           STATUS_LABELS.get(head[8], head[8]) if idx == 0 else None,
                           head[10] if idx == 0 else None,
                           ("Pemerintah" if head[11] == "PEMERINTAH" else "Umum") if idx == 0 else None,
                           float(head[12]) if idx == 0 else None])
        last = ws.max_row
        ws.append([None, None, "TOTAL", None, None, None, None, None, None, None,
                   f"=SUM(K2:K{last})", f"=SUM(L2:L{last})", f"=SUM(M2:M{last})", f"=SUM(N2:N{last})"])
        for cell in ws[ws.max_row]:
            cell.font = Font(name="Arial", bold=True)
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                if cell.font is None or not cell.font.bold:
                    cell.font = Font(name="Arial", size=10)
                if 10 <= cell.column <= 14 or cell.column == 20:
                    cell.number_format = "#,##0"
        widths = [5, 18, 42, 48, 30, 10, 8, 8, 8, 14, 14, 14, 14, 12, 10, 20, 14, 20, 12, 14]
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
