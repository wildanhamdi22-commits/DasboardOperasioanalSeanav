"""Modul Penjualan: katalog produk, pesanan indent, barang datang (SN), QC, DO, peta."""
import json
import logging
from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib import messages
from django.db import DatabaseError, connection, transaction
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.utils import timezone

from .forms import EQUIPMENT_CATEGORIES
from .modules import QC_CHECKLIST, as_json, attachments, save_attachment, validate_upload
from .views import _current_pengguna_id, business_permission_required

logger = logging.getLogger(__name__)

ORDER_STEPS = [
    ("BARU", "Pesanan dibuat"),
    ("INDENT", "Indent ke pabrikan"),
    ("DATANG", "Barang datang"),
    ("SIAP_KIRIM", "QC lulus"),
    ("DIKIRIM", "Terkirim"),
]
ORDER_LABELS = dict(ORDER_STEPS)
QC_LABELS = {"BELUM": "Belum dicek", "LULUS": "Lulus", "CATATAN": "Lulus dengan catatan", "GAGAL": "Tidak lulus"}


# --------------------------------------------------------------------------- katalog
class ProductForm(forms.Form):
    category = forms.ChoiceField(label="Kategori", choices=[("", "Pilih kategori...")] + [(c, c) for c in EQUIPMENT_CATEGORIES])
    brand = forms.CharField(label="Merek", max_length=120, required=False)
    name = forms.CharField(label="Nama produk / model", max_length=200)
    price = forms.DecimalField(label="Harga jual (Rp)", min_value=Decimal("0"), max_digits=18, decimal_places=2)
    unit = forms.CharField(label="Satuan", max_length=40, initial="unit")
    lead_time = forms.CharField(label="Perkiraan waktu indent", max_length=80, required=False,
                                help_text="Contoh: 2–3 minggu setelah PO.")
    warranty = forms.CharField(label="Garansi", max_length=120, required=False, help_text="Contoh: 1 tahun dari pabrikan.")
    description = forms.CharField(label="Deskripsi / spesifikasi singkat", required=False,
                                  widget=forms.Textarea(attrs={"rows": 3}))


def _category_id(cursor, name):
    cursor.execute("SELECT id FROM kategori_alat WHERE nama = %s", [name])
    row = cursor.fetchone()
    if row:
        return row[0]
    cursor.execute("INSERT INTO kategori_alat (nama) VALUES (%s) RETURNING id", [name])
    return cursor.fetchone()[0]


@business_permission_required("INVENTARIS", "LIHAT")
def sales_catalog(request):
    category = request.GET.get("kategori", "")
    search = request.GET.get("q", "").strip()[:100]
    show_inactive = request.GET.get("arsip") == "1"
    products = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT p.id, k.nama, COALESCE(p.merek, ''), p.nama, p.harga_jual, p.satuan,
                       COALESCE(p.waktu_indent, ''), COALESCE(p.garansi, ''), COALESCE(p.deskripsi, ''), p.aktif,
                       (SELECT COUNT(*) FROM quotation_item qi WHERE qi.produk_jual_id = p.id),
                       (SELECT COUNT(*) FROM unit_penjualan up JOIN pesanan_penjualan_item ppi ON ppi.id = up.pesanan_item_id
                        WHERE ppi.produk_jual_id = p.id)
                FROM produk_jual p JOIN kategori_alat k ON k.id = p.kategori_alat_id
                WHERE (%s OR p.aktif) AND (%s = '' OR k.nama = %s)
                  AND (%s = '' OR concat_ws(' ', p.merek, p.nama, p.deskripsi) ILIKE %s)
                ORDER BY k.nama, p.merek, p.nama
                """,
                [show_inactive, category, category, search, f"%{search}%"],
            )
            products = [{"id": r[0], "category": r[1], "brand": r[2], "name": r[3], "price": r[4], "unit": r[5],
                         "lead_time": r[6], "warranty": r[7], "description": r[8], "active": r[9],
                         "quoted": r[10], "sold": r[11]} for r in cursor.fetchall()]
    except DatabaseError:
        messages.error(request, "Katalog belum dapat dibaca. Jalankan migrasi database.")
    return render(request, "portal/sales_catalog.html", {
        "products": products, "categories": EQUIPMENT_CATEGORIES, "category": category, "search": search,
        "show_inactive": show_inactive,
    })


@business_permission_required("INVENTARIS", lambda request, product_id=None: "BUAT" if product_id is None else "UBAH")
def sales_catalog_form(request, product_id=None):
    initial = {}
    if product_id:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT k.nama, p.merek, p.nama, p.harga_jual, p.satuan, p.waktu_indent, p.garansi, p.deskripsi
                FROM produk_jual p JOIN kategori_alat k ON k.id = p.kategori_alat_id WHERE p.id = %s
                """,
                [product_id],
            )
            row = cursor.fetchone()
        if not row:
            raise Http404("Produk tidak ditemukan.")
        initial = dict(zip(["category", "brand", "name", "price", "unit", "lead_time", "warranty", "description"], row))
        if initial["category"] not in EQUIPMENT_CATEGORIES:
            initial["category"] = ""
    form = ProductForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cat = _category_id(cursor, d["category"])
                user_id = _current_pengguna_id(cursor, request)
                values = [cat, d["brand"] or None, d["name"], d["price"], d["unit"] or "unit",
                          d["lead_time"] or None, d["warranty"] or None, d["description"] or None]
                if product_id:
                    cursor.execute(
                        """
                        UPDATE produk_jual SET kategori_alat_id = %s, merek = %s, nama = %s, harga_jual = %s,
                               satuan = %s, waktu_indent = %s, garansi = %s, deskripsi = %s WHERE id = %s
                        """,
                        values + [product_id],
                    )
                else:
                    cursor.execute(
                        """
                        INSERT INTO produk_jual (kategori_alat_id, merek, nama, harga_jual, satuan, waktu_indent,
                                                 garansi, deskripsi, dibuat_oleh)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        values + [user_id],
                    )
            messages.success(request, "Produk katalog tersimpan.")
            return redirect("sales_catalog")
        except DatabaseError:
            logger.exception("Simpan produk gagal")
            form.add_error(None, "Produk belum tersimpan. Periksa koneksi database.")
    return render(request, "portal/sales_catalog_form.html", {"form": form, "product_id": product_id})


@business_permission_required("INVENTARIS", "UBAH")
def sales_catalog_delete(request, product_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT nama, aktif FROM produk_jual WHERE id = %s FOR UPDATE", [product_id])
            row = cursor.fetchone()
            if not row:
                raise Http404("Produk tidak ditemukan.")
            if request.POST.get("action") == "aktifkan":
                cursor.execute("UPDATE produk_jual SET aktif = TRUE WHERE id = %s", [product_id])
                messages.success(request, f"{row[0]} kembali aktif di katalog.")
                return redirect("sales_catalog")
            cursor.execute(
                """
                SELECT (SELECT COUNT(*) FROM quotation_item WHERE produk_jual_id = %s)
                     + (SELECT COUNT(*) FROM pesanan_penjualan_item WHERE produk_jual_id = %s)
                """,
                [product_id, product_id],
            )
            if cursor.fetchone()[0]:
                cursor.execute("UPDATE produk_jual SET aktif = FALSE WHERE id = %s", [product_id])
                messages.success(request, f"{row[0]} sudah dipakai di quotation/pesanan, jadi diarsipkan dari katalog.")
            else:
                cursor.execute("DELETE FROM produk_jual WHERE id = %s", [product_id])
                messages.success(request, f"{row[0]} dihapus dari katalog.")
    except DatabaseError:
        messages.error(request, "Produk belum dapat dihapus.")
    return redirect("sales_catalog")


# --------------------------------------------------------------------------- pesanan
def _refresh_order_status(cursor, order_id):
    cursor.execute("SELECT status FROM pesanan_penjualan WHERE id = %s", [order_id])
    status = cursor.fetchone()[0]
    if status in ("DIKIRIM", "DIBATALKAN"):
        return status
    cursor.execute(
        """
        SELECT COALESCE(SUM(CASE WHEN ppi.produk_jual_id IS NOT NULL OR ppi.model_alat_id IS NOT NULL
                                 THEN CEIL(ppi.kuantitas) ELSE 0 END), 0),
               (SELECT COUNT(*) FROM unit_penjualan up JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
                WHERE i.pesanan_id = %s),
               (SELECT COUNT(*) FROM unit_penjualan up JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
                WHERE i.pesanan_id = %s AND up.status_qc IN ('LULUS', 'CATATAN'))
        FROM pesanan_penjualan_item ppi WHERE ppi.pesanan_id = %s
        """,
        [order_id, order_id, order_id],
    )
    expected, received, passed = cursor.fetchone()
    cursor.execute("SELECT pemasok IS NOT NULL OR no_po_pemasok IS NOT NULL FROM pesanan_penjualan WHERE id = %s", [order_id])
    ordered = cursor.fetchone()[0]
    if received and received >= expected and passed >= received:
        new = "SIAP_KIRIM"
    elif received and received >= expected:
        new = "DATANG"
    elif ordered or received:
        new = "INDENT"
    else:
        new = "BARU"
    if new != status:
        cursor.execute("UPDATE pesanan_penjualan SET status = %s WHERE id = %s", [new, order_id])
    return new


@business_permission_required("SERAH_TERIMA", "BUAT")
def sales_order_create(request, quotation_id):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT q.id, q.nomor, q.status, q.perusahaan_id, q.klien_id, q.total, COALESCE(kl.alamat, '')
                FROM quotation q JOIN klien kl ON kl.id = q.klien_id
                WHERE q.id = %s AND q.jenis = 'PENJUALAN' FOR UPDATE OF q
                """,
                [quotation_id],
            )
            q = cursor.fetchone()
            if not q:
                raise Http404("Quotation penjualan tidak ditemukan.")
            cursor.execute("SELECT id FROM pesanan_penjualan WHERE quotation_id = %s", [quotation_id])
            existing = cursor.fetchone()
            if existing:
                return redirect("sales_order_detail", order_id=existing[0])
            if q[2] != "DITERIMA_KLIEN":
                messages.error(request, "Pesanan dibuat setelah quotation disetujui klien.")
                return redirect("sales_quotations")
            user_id = _current_pengguna_id(cursor, request)
            cursor.execute(
                """
                INSERT INTO pesanan_penjualan (perusahaan_id, nomor_pesanan, quotation_id, klien_id, tanggal_pesanan,
                                               status, total, alamat_kirim, dibuat_oleh)
                VALUES (%s, %s, %s, %s, CURRENT_DATE, 'BARU', %s, %s, %s) RETURNING id
                """,
                [q[3], f"SO/{q[1]}"[:80], quotation_id, q[4], q[5], q[6] or None, user_id],
            )
            order_id = cursor.fetchone()[0]
            cursor.execute(
                """
                INSERT INTO pesanan_penjualan_item (pesanan_id, quotation_item_id, model_alat_id, produk_jual_id,
                                                    deskripsi_snapshot, kuantitas, harga_satuan, subtotal)
                SELECT %s, qi.id, qi.model_alat_id, qi.produk_jual_id, qi.deskripsi_item, qi.kuantitas,
                       qi.harga_satuan, qi.subtotal
                FROM quotation_item qi WHERE qi.quotation_id = %s ORDER BY qi.id
                """,
                [order_id, quotation_id],
            )
        messages.success(request, f"Pesanan SO/{q[1]} dibuat. Lanjutkan dengan mencatat indent ke pabrikan.")
        return redirect("sales_order_detail", order_id=order_id)
    except DatabaseError:
        logger.exception("Buat pesanan gagal")
        messages.error(request, "Pesanan belum dapat dibuat.")
        return redirect("sales_quotations")


@business_permission_required("SERAH_TERIMA", "LIHAT")
def sales_orders(request):
    status = request.GET.get("status", "")
    orders = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pp.id, pp.nomor_pesanan, co.kode, kl.nama_perusahaan, pp.tanggal_pesanan, pp.status,
                       pp.eta, COALESCE(pp.pemasok, ''), pp.total,
                       (SELECT COALESCE(SUM(CEIL(i.kuantitas)), 0) FROM pesanan_penjualan_item i
                        WHERE i.pesanan_id = pp.id AND (i.produk_jual_id IS NOT NULL OR i.model_alat_id IS NOT NULL)),
                       (SELECT COUNT(*) FROM unit_penjualan up JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
                        WHERE i.pesanan_id = pp.id),
                       (SELECT COUNT(*) FROM unit_penjualan up JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
                        WHERE i.pesanan_id = pp.id AND up.status_qc IN ('LULUS', 'CATATAN')),
                       (SELECT COUNT(*) FROM lampiran_dokumen ld WHERE ld.entitas = 'pesanan_penjualan'
                        AND ld.entitas_id = pp.id AND ld.jenis = 'DO_TTD'),
                       q.nomor, pp.eta < CURRENT_DATE AND pp.status IN ('BARU', 'INDENT')
                FROM pesanan_penjualan pp
                JOIN perusahaan co ON co.id = pp.perusahaan_id
                JOIN klien kl ON kl.id = pp.klien_id
                LEFT JOIN quotation q ON q.id = pp.quotation_id
                WHERE (%s = '' OR pp.status = %s)
                ORDER BY pp.dibuat_pada DESC LIMIT 300
                """,
                [status, status],
            )
            for r in cursor.fetchall():
                orders.append({"id": r[0], "number": r[1], "company": r[2], "client": r[3], "date": r[4],
                               "status": r[5], "status_label": ORDER_LABELS.get(r[5], r[5].title()), "eta": r[6],
                               "supplier": r[7], "total": r[8], "expected": int(r[9]), "received": r[10],
                               "passed": r[11], "signed_do": r[12], "quotation": r[13], "late": r[14],
                               "step": [s for s, _ in ORDER_STEPS].index(r[5]) if r[5] in ORDER_LABELS else 0})
    except DatabaseError:
        messages.error(request, "Daftar pesanan belum dapat dibaca. Jalankan migrasi database.")
    return render(request, "portal/sales_orders.html", {
        "orders": orders, "status": status, "steps": ORDER_STEPS,
    })


def _order_context(cursor, order_id):
    cursor.execute(
        """
        SELECT pp.id, pp.nomor_pesanan, co.kode, kl.nama_perusahaan, pp.tanggal_pesanan, pp.status,
               COALESCE(pp.pemasok, ''), COALESCE(pp.no_po_pemasok, ''), pp.tanggal_pesan_pemasok, pp.eta,
               COALESCE(pp.alamat_kirim, ''), pp.latitude, pp.longitude, pp.tanggal_kirim,
               COALESCE(pp.penerima, ''), COALESCE(pp.pengirim, ''), q.id, q.nomor, pp.total, COALESCE(pp.catatan, '')
        FROM pesanan_penjualan pp
        JOIN perusahaan co ON co.id = pp.perusahaan_id
        JOIN klien kl ON kl.id = pp.klien_id
        LEFT JOIN quotation q ON q.id = pp.quotation_id
        WHERE pp.id = %s
        """,
        [order_id],
    )
    r = cursor.fetchone()
    if not r:
        raise Http404("Pesanan tidak ditemukan.")
    order = dict(zip(["id", "number", "company", "client", "date", "status", "supplier", "supplier_po",
                      "supplier_date", "eta", "address", "lat", "lng", "shipped_at", "receiver", "sender",
                      "quotation_id", "quotation", "total", "notes"], r))
    order["status_label"] = ORDER_LABELS.get(order["status"], order["status"])
    order["step"] = [s for s, _ in ORDER_STEPS].index(order["status"]) if order["status"] in ORDER_LABELS else 0
    cursor.execute(
        """
        SELECT ppi.id, ppi.deskripsi_snapshot, ppi.kuantitas, COALESCE(qi.satuan, 'unit'),
               ppi.produk_jual_id IS NOT NULL OR ppi.model_alat_id IS NOT NULL
        FROM pesanan_penjualan_item ppi
        LEFT JOIN quotation_item qi ON qi.id = ppi.quotation_item_id
        WHERE ppi.pesanan_id = %s ORDER BY ppi.id
        """,
        [order_id],
    )
    items = []
    for r in cursor.fetchall():
        items.append({"id": r[0], "name": r[1], "qty": r[2], "unit": r[3], "tracked": r[4],
                      "expected": int(Decimal(r[2]).to_integral_value(rounding="ROUND_CEILING")) if r[4] else 0,
                      "units": []})
    cursor.execute(
        """
        SELECT up.id, up.pesanan_item_id, up.nomor_seri, COALESCE(up.sn_packing_list, ''), up.tanggal_datang,
               up.status_qc, qc.diperiksa_pada, COALESCE(pg.nama, '')
        FROM unit_penjualan up
        JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
        LEFT JOIN LATERAL (SELECT * FROM qc_penjualan q WHERE q.unit_penjualan_id = up.id
                           ORDER BY q.diperiksa_pada DESC LIMIT 1) qc ON TRUE
        LEFT JOIN pengguna pg ON pg.id = qc.pemeriksa_id
        WHERE i.pesanan_id = %s ORDER BY up.id
        """,
        [order_id],
    )
    by_item = {i["id"]: i for i in items}
    all_units = []
    for r in cursor.fetchall():
        unit = {"id": r[0], "serial": r[2], "packing_sn": r[3], "arrived": r[4], "qc": r[5],
                "qc_label": QC_LABELS.get(r[5], r[5]), "qc_at": r[6], "qc_by": r[7],
                "mismatch": bool(r[3]) and r[3].strip().upper() != r[2].strip().upper()}
        by_item[r[1]]["units"].append(unit)
        all_units.append(unit)
    expected = sum(i["expected"] for i in items)
    received = len(all_units)
    passed = sum(1 for u in all_units if u["qc"] in ("LULUS", "CATATAN"))
    return order, items, {"expected": expected, "received": received, "passed": passed,
                          "failed": sum(1 for u in all_units if u["qc"] == "GAGAL"),
                          "ready": received >= expected and received > 0 and passed == received}


def _dec(value):
    try:
        return Decimal(value) if value not in (None, "") else None
    except InvalidOperation:
        return None


@business_permission_required("SERAH_TERIMA", "LIHAT")
def sales_order_detail(request, order_id):
    try:
        with connection.cursor() as cursor:
            order, items, counts = _order_context(cursor, order_id)
    except DatabaseError:
        messages.error(request, "Pesanan belum dapat dibaca.")
        return redirect("sales_orders")

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                user_id = _current_pengguna_id(cursor, request)
                if order["status"] == "DIKIRIM" and action not in ("upload",):
                    messages.error(request, "Pesanan sudah terkirim; data tidak dapat diubah.")
                elif action == "indent":
                    cursor.execute(
                        """
                        UPDATE pesanan_penjualan SET pemasok = NULLIF(%s, ''), no_po_pemasok = NULLIF(%s, ''),
                               tanggal_pesan_pemasok = %s, eta = %s, catatan = NULLIF(%s, '')
                        WHERE id = %s
                        """,
                        [request.POST.get("supplier", "").strip(), request.POST.get("supplier_po", "").strip(),
                         request.POST.get("supplier_date") or None, request.POST.get("eta") or None,
                         request.POST.get("notes", "").strip(), order_id],
                    )
                    _refresh_order_status(cursor, order_id)
                    messages.success(request, "Data indent ke pabrikan tersimpan.")
                elif action == "receive":
                    item_id = int(request.POST.get("item_id", "0") or 0)
                    item = next((i for i in items if i["id"] == item_id and i["tracked"]), None)
                    lines = [l.strip() for l in request.POST.get("serials", "").splitlines() if l.strip()]
                    arrived = request.POST.get("arrived") or timezone.localdate()
                    if not item or not lines:
                        messages.error(request, "Pilih item dan isi minimal satu serial number.")
                    else:
                        room = item["expected"] - len(item["units"])
                        added = warnings = 0
                        for line in lines[:max(room, 0)]:
                            parts = [p.strip() for p in line.replace("\t", "|").replace(";", "|").split("|")]
                            serial, packing = parts[0][:120], (parts[1][:120] if len(parts) > 1 else "")
                            if not serial:
                                continue
                            cursor.execute(
                                """
                                INSERT INTO unit_penjualan (pesanan_item_id, nomor_seri, sn_packing_list, tanggal_datang,
                                                            dicatat_oleh)
                                VALUES (%s, %s, NULLIF(%s, ''), %s, %s)
                                ON CONFLICT (pesanan_item_id, nomor_seri) DO NOTHING
                                """,
                                [item_id, serial, packing, arrived, user_id],
                            )
                            added += cursor.rowcount
                            if packing and packing.upper() != serial.upper():
                                warnings += 1
                        _refresh_order_status(cursor, order_id)
                        if len(lines) > room:
                            messages.warning(request, f"Hanya {max(room, 0)} unit yang dicatat sesuai jumlah pesanan.")
                        if warnings:
                            messages.warning(request, f"{warnings} serial number tidak sama dengan packing list — periksa fisik unit saat QC.")
                        messages.success(request, f"{added} unit dicatat datang. Lanjutkan dengan QC setiap unit.")
                elif action == "remove_unit":
                    cursor.execute(
                        """
                        DELETE FROM unit_penjualan up USING pesanan_penjualan_item i
                        WHERE up.id = %s AND i.id = up.pesanan_item_id AND i.pesanan_id = %s AND up.status_qc = 'BELUM'
                        """,
                        [request.POST.get("unit_id"), order_id],
                    )
                    _refresh_order_status(cursor, order_id)
                    messages.success(request, "Unit dihapus dari catatan barang datang." if cursor.rowcount
                                     else "Unit yang sudah di-QC tidak dapat dihapus.")
                elif action == "ship":
                    status = _refresh_order_status(cursor, order_id)
                    receiver = request.POST.get("receiver", "").strip()
                    if status != "SIAP_KIRIM":
                        messages.error(request, "Barang baru bisa dikirim setelah semua unit datang dan lulus QC.")
                    elif not receiver:
                        messages.error(request, "Isi nama penerima di pihak klien.")
                    else:
                        shipped = request.POST.get("shipped_at") or timezone.localtime().strftime("%Y-%m-%dT%H:%M")
                        cursor.execute(
                            """
                            UPDATE pesanan_penjualan SET status = 'DIKIRIM', tanggal_kirim = %s, penerima = %s,
                                   pengirim = NULLIF(%s, ''), alamat_kirim = NULLIF(%s, ''), latitude = %s, longitude = %s
                            WHERE id = %s
                            """,
                            [shipped, receiver, request.POST.get("sender", "").strip(),
                             request.POST.get("address", "").strip(), _dec(request.POST.get("lat")),
                             _dec(request.POST.get("lng")), order_id],
                        )
                        cursor.execute(
                            """
                            INSERT INTO serah_terima_penjualan (pesanan_id, tanggal, diserahkan_oleh, diterima_oleh, catatan)
                            VALUES (%s, %s, %s, %s, NULLIF(%s, '')) RETURNING id
                            """,
                            [order_id, shipped, user_id, receiver, request.POST.get("ship_notes", "").strip()],
                        )
                        handover_id = cursor.fetchone()[0]
                        cursor.execute(
                            """
                            INSERT INTO serah_terima_penjualan_item (serah_terima_id, pesanan_item_id, kuantitas)
                            SELECT %s, i.id, i.kuantitas FROM pesanan_penjualan_item i WHERE i.pesanan_id = %s
                            """,
                            [handover_id, order_id],
                        )
                        messages.success(request, "Barang dicatat terkirim. Unduh DO, minta tanda tangan penerima, lalu unggah ke arsip.")
                elif action == "location":
                    cursor.execute(
                        "UPDATE pesanan_penjualan SET alamat_kirim = NULLIF(%s, ''), latitude = %s, longitude = %s WHERE id = %s",
                        [request.POST.get("address", "").strip(), _dec(request.POST.get("lat")),
                         _dec(request.POST.get("lng")), order_id],
                    )
                    messages.success(request, "Lokasi pengiriman diperbarui.")
                elif action == "upload":
                    upload = request.FILES.get("file")
                    problem = validate_upload(upload)
                    if problem:
                        messages.error(request, problem)
                    else:
                        save_attachment(cursor, request, "pesanan_penjualan", order_id,
                                        request.POST.get("jenis") or "DO_TTD", upload,
                                        request.POST.get("title"), None, user_id)
                        messages.success(request, "Dokumen tersimpan di arsip pesanan.")
        except DatabaseError:
            logger.exception("Aksi pesanan %s gagal", order_id)
            messages.error(request, "Perubahan belum tersimpan. Periksa data dan coba lagi.")
        return redirect("sales_order_detail", order_id=order_id)

    with connection.cursor() as cursor:
        files = attachments(cursor, "pesanan_penjualan", [order_id]).get(order_id, [])
    return render(request, "portal/sales_order_detail.html", {
        "order": order, "items": items, "counts": counts, "steps": ORDER_STEPS, "files": files,
        "signed": [f for f in files if f["kind"] == "DO_TTD"],
        "now": timezone.localtime().strftime("%Y-%m-%dT%H:%M"), "today": timezone.localdate().isoformat(),
    })


# --------------------------------------------------------------------------- QC
@business_permission_required("SERAH_TERIMA", "LIHAT")
def sales_qc_list(request):
    pending, done = [], []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT up.id, up.nomor_seri, i.deskripsi_snapshot, pp.id, pp.nomor_pesanan, kl.nama_perusahaan,
                       up.tanggal_datang, up.status_qc, qc.diperiksa_pada, COALESCE(pg.nama, ''),
                       COALESCE(up.sn_packing_list, '')
                FROM unit_penjualan up
                JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
                JOIN pesanan_penjualan pp ON pp.id = i.pesanan_id
                JOIN klien kl ON kl.id = pp.klien_id
                LEFT JOIN LATERAL (SELECT * FROM qc_penjualan q WHERE q.unit_penjualan_id = up.id
                                   ORDER BY q.diperiksa_pada DESC LIMIT 1) qc ON TRUE
                LEFT JOIN pengguna pg ON pg.id = qc.pemeriksa_id
                ORDER BY up.status_qc = 'BELUM' DESC, COALESCE(qc.diperiksa_pada, up.dicatat_pada) DESC
                LIMIT 300
                """
            )
            for r in cursor.fetchall():
                row = {"id": r[0], "serial": r[1], "name": r[2], "order_id": r[3], "order": r[4], "client": r[5],
                       "arrived": r[6], "qc": r[7], "qc_label": QC_LABELS.get(r[7], r[7]), "qc_at": r[8], "qc_by": r[9],
                       "mismatch": bool(r[10]) and r[10].upper() != r[1].upper()}
                (pending if r[7] in ("BELUM", "GAGAL") else done).append(row)
    except DatabaseError:
        messages.error(request, "Data QC belum dapat dibaca. Jalankan migrasi database.")
    return render(request, "portal/sales_qc_list.html", {"pending": pending, "done": done})


@business_permission_required("SERAH_TERIMA", "UBAH")
def sales_qc_form(request, unit_id):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT up.id, up.nomor_seri, COALESCE(up.sn_packing_list, ''), i.deskripsi_snapshot, pp.id,
                   pp.nomor_pesanan, kl.nama_perusahaan, up.status_qc, pp.status
            FROM unit_penjualan up
            JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
            JOIN pesanan_penjualan pp ON pp.id = i.pesanan_id
            JOIN klien kl ON kl.id = pp.klien_id
            WHERE up.id = %s
            """,
            [unit_id],
        )
        unit = cursor.fetchone()
        if not unit:
            raise Http404("Unit tidak ditemukan.")
        cursor.execute(
            """
            SELECT q.id, q.hasil, q.diperiksa_pada, COALESCE(p.nama, ''), COALESCE(q.catatan, ''),
                   COALESCE(q.firmware, ''), q.checklist
            FROM qc_penjualan q LEFT JOIN pengguna p ON p.id = q.pemeriksa_id
            WHERE q.unit_penjualan_id = %s ORDER BY q.diperiksa_pada DESC
            """,
            [unit_id],
        )
        history = [{"id": r[0], "result": r[1], "label": QC_LABELS.get(r[1], r[1]), "at": r[2], "by": r[3],
                    "notes": r[4], "firmware": r[5], "checklist": as_json(r[6]) or {}} for r in cursor.fetchall()]
        photos = attachments(cursor, "unit_penjualan", [unit_id]).get(unit_id, [])
    last = history[0] if history else None
    error = None
    if request.method == "POST":
        if unit[8] == "DIKIRIM":
            error = "Pesanan sudah terkirim; QC tidak dapat diubah."
        else:
            result = request.POST.get("result")
            checklist = {key: request.POST.get(f"chk_{key}") == "on" for _, items in QC_CHECKLIST for key, _ in items}
            unchecked = [key for key, ok in checklist.items() if not ok]
            notes = request.POST.get("notes", "").strip()
            if result not in ("LULUS", "CATATAN", "GAGAL"):
                error = "Pilih hasil QC."
            elif result == "LULUS" and unchecked:
                error = "Hasil Lulus memerlukan semua poin checklist tercentang. Gunakan 'Lulus dengan catatan' bila ada pengecualian."
            elif result in ("CATATAN", "GAGAL") and len(notes) < 5:
                error = "Tuliskan catatan untuk hasil ini."
            else:
                try:
                    with transaction.atomic(), connection.cursor() as cursor:
                        user_id = _current_pengguna_id(cursor, request)
                        cursor.execute(
                            """
                            INSERT INTO qc_penjualan (unit_penjualan_id, checklist, firmware, hasil, catatan, pemeriksa_id)
                            VALUES (%s, %s::jsonb, NULLIF(%s, ''), %s, NULLIF(%s, ''), %s) RETURNING id
                            """,
                            [unit_id, json.dumps(checklist), request.POST.get("firmware", "").strip()[:80], result,
                             notes, user_id],
                        )
                        cursor.execute("UPDATE unit_penjualan SET status_qc = %s WHERE id = %s", [result, unit_id])
                        for photo in request.FILES.getlist("photos")[:10]:
                            if validate_upload(photo) is None:
                                save_attachment(cursor, request, "unit_penjualan", unit_id, "FOTO_QC", photo,
                                                user_id=user_id)
                        _refresh_order_status(cursor, unit[4])
                    messages.success(request, f"QC unit {unit[1]} tersimpan: {QC_LABELS[result]}.")
                    return redirect("sales_order_detail", order_id=unit[4])
                except DatabaseError:
                    logger.exception("QC gagal")
                    error = "Hasil QC belum tersimpan."
    return render(request, "portal/sales_qc_form.html", {
        "unit": unit, "checklist": QC_CHECKLIST, "history": history, "last": last, "photos": photos, "error": error,
        "mismatch": bool(unit[2]) and unit[2].upper() != unit[1].upper(), "qc_label": QC_LABELS.get(unit[7], unit[7]),
    })


# --------------------------------------------------------------------------- DO & peta
@business_permission_required("SERAH_TERIMA", "LIHAT")
def sales_deliveries(request):
    rows = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pp.id, pp.nomor_pesanan, co.kode, kl.nama_perusahaan, pp.status, pp.tanggal_kirim,
                       COALESCE(pp.penerima, ''), COALESCE(pp.alamat_kirim, ''),
                       (SELECT COUNT(*) FROM lampiran_dokumen ld WHERE ld.entitas = 'pesanan_penjualan'
                        AND ld.entitas_id = pp.id AND ld.jenis = 'DO_TTD'),
                       (SELECT COUNT(*) FROM unit_penjualan up JOIN pesanan_penjualan_item i ON i.id = up.pesanan_item_id
                        WHERE i.pesanan_id = pp.id)
                FROM pesanan_penjualan pp
                JOIN perusahaan co ON co.id = pp.perusahaan_id
                JOIN klien kl ON kl.id = pp.klien_id
                WHERE pp.status IN ('SIAP_KIRIM', 'DIKIRIM')
                ORDER BY pp.status = 'SIAP_KIRIM' DESC, pp.tanggal_kirim DESC NULLS FIRST
                """
            )
            rows = [{"id": r[0], "number": r[1], "company": r[2], "client": r[3], "status": r[4],
                     "status_label": ORDER_LABELS.get(r[4], r[4]), "shipped_at": r[5], "receiver": r[6],
                     "address": r[7], "signed": r[8], "units": r[9]} for r in cursor.fetchall()]
    except DatabaseError:
        messages.error(request, "Data DO penjualan belum dapat dibaca.")
    return render(request, "portal/sales_deliveries.html", {"rows": rows})


@business_permission_required("SERAH_TERIMA", "LIHAT")
def sales_map(request):
    points = []
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT pp.id, pp.nomor_pesanan, kl.nama_perusahaan, pp.status, COALESCE(pp.alamat_kirim, ''),
                       pp.latitude, pp.longitude, pp.tanggal_kirim, pp.tanggal_pesanan, co.kode,
                       (SELECT string_agg(i.deskripsi_snapshot || ' ×' || trim_scale(i.kuantitas)::text, ', ')
                        FROM pesanan_penjualan_item i WHERE i.pesanan_id = pp.id)
                FROM pesanan_penjualan pp
                JOIN klien kl ON kl.id = pp.klien_id
                JOIN perusahaan co ON co.id = pp.perusahaan_id
                WHERE pp.status <> 'DIBATALKAN'
                ORDER BY COALESCE(pp.tanggal_kirim::date, pp.tanggal_pesanan) DESC
                """
            )
            for r in cursor.fetchall():
                when = timezone.localtime(r[7]).strftime("%d %b %Y") if r[7] else f"Dipesan {r[8]:%d %b %Y}"
                points.append({"id": r[0], "title": r[1], "client": r[2],
                               "status": "DIKIRIM" if r[3] == "DIKIRIM" else "PROSES",
                               "location": r[4], "lat": float(r[5]) if r[5] is not None else None,
                               "lng": float(r[6]) if r[6] is not None else None, "period": when,
                               "detail": r[10] or "", "company": r[9], "url": f"/penjualan/pesanan/{r[0]}/"})
    except DatabaseError:
        messages.error(request, "Data peta penjualan belum dapat dibaca.")
    return render(request, "portal/module_map.html", {
        "title": "Peta Penjualan", "crumb": "Penjualan · Peta Penjualan",
        "intro": "Lokasi klien dan alamat pengiriman semua produk yang terjual.",
        "points": points,
        "legend": [("DIKIRIM", "Sudah terkirim", "#1f9d63"), ("PROSES", "Dalam proses", "#a9682b")],
        "missing_hint": "Isi lokasi pengiriman di halaman detail pesanan.",
    })
