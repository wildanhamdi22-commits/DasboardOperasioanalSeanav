"""Dokumen pinjam bendera untuk klien instansi pemerintah (bahasa Indonesia).

* Kwitansi format instansi: blok kementerian/satker, tahun anggaran & DIPA, "Terima dari Bendahara
  Pengeluaran", uang sejumlah + terbilang, untuk keperluan, tanda tangan "Yang membayarkan" (bendahara)
  dan "Yang menerima" (perusahaan, ruang meterai), serta kotak rincian BRUTO / potongan / NETO.
* Berita Acara Serah Terima Pekerjaan berkop perusahaan.
"""
from __future__ import annotations

from decimal import Decimal

from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Mm

from .doc_builder import fmt_idr, keep_row_together, para_spacing, set_cell_margins, set_cell_shading, table_fixed, \
    table_no_borders
from .lpj_builder import BLACK, SOFT, LpjDoc, _border, _grid, qty

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober",
         "November", "Desember"]


def tgl_id(d):
    return f"{d.day} {BULAN[d.month - 1]} {d.year}" if d else "-"


def rp_id(value, cents=True):
    return f"Rp {fmt_idr(value)}" + (",00" if cents else "")


def _blank_date(d):
    """Hari dikosongkan untuk diisi bendahara saat pembayaran: 'Jakarta, ........ November 2026'."""
    return f"Jakarta, ........ {BULAN[d.month - 1]} {d.year}"


def _kv_table(d, cell, rows, widths, size=10, first_bold=False):
    """Tabel 'Label : isi' di dalam sel."""
    t = cell.add_table(rows=len(rows), cols=3)
    table_no_borders(t, zero_cell_margins=True)
    table_fixed(t, widths)
    for i, (row, (label, value)) in enumerate(zip(t.rows, rows)):
        keep_row_together(row)
        for c, txt in zip(row.cells, (label, ":", value)):
            set_cell_margins(c, 6, 6, 0, 30)
            lines = txt if isinstance(txt, list) else [txt]
            for k, line in enumerate(lines):
                p = c.paragraphs[0] if k == 0 else c.add_paragraph()
                para_spacing(p, 0, 0, 1.2)
                r = d.run(p, str(line or "-"), size=size, bold=first_bold and i == 0 and c is row.cells[2],
                          color=BLACK)
                if first_bold and i == 0 and c is row.cells[2]:
                    r.font.underline = True
    return t


def _company_npwp(brand):
    for part in brand.contact.split("||"):
        if "NPWP" in part:
            return part.split(":", 1)[-1].strip()
    return "-"


def build_gov_receipt(r) -> bytes:
    d = LpjDoc(r["brand"], kop=False)
    g = r["gov"]
    s = r["sums"]
    # ---- kepala: kementerian / satker (kiri), nomor (kanan)
    t = d.doc.add_table(rows=1, cols=2)
    table_no_borders(t, zero_cell_margins=True)
    table_fixed(t, [d.content_w - 62, 62])
    left, right = t.rows[0].cells
    p = left.paragraphs[0]
    para_spacing(p, 0, 1, 1.2)
    d.run(p, (g.get("kementerian") or "").upper() or " ", size=10.5, bold=True, color=BLACK)
    _kv_table(d, left, [("SATKER", r["institution"] or "-"), ("KODE SATKER", g.get("kode_satker") or "-")],
              [30, 4, d.content_w - 62 - 34], size=9.5)
    q = right.paragraphs[0]
    q.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    para_spacing(q, 0, 0, 1.2)
    d.run(q, "Bukti Kas", size=9, color=SOFT)
    q = right.add_paragraph()
    q.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    para_spacing(q, 0, 0, 1.2)
    d.run(q, f"Nomor: {r['kw_no']}", size=9, color=BLACK)
    # ---- judul
    p = d.text("KWITANSI", size=20, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, before=8, after=8, underline=True)
    p.runs[0].font.spacing = Mm(0.9)
    d.fields([("Tahun Anggaran", g.get("tahun_anggaran") or str(r["doc_date"].year)),
              ("Kode Program dan Anggaran", g.get("kode_dipa") or "-"),
              ("Jenis Pengeluaran", g.get("jenis_pengeluaran") or "-")], widths=(52, 4, None), size=9.5, after=4)
    # ---- isi kwitansi dalam bingkai
    outer = d.doc.add_table(rows=1, cols=1)
    table_fixed(outer, [d.content_w])
    cell = outer.rows[0].cells[0]
    line = ("single", 8, "222222")
    _border(cell, top=line, bottom=line, left=line, right=line)
    set_cell_margins(cell, 120, 120, 160, 160)
    inner = d.content_w - 6
    payer = g.get("bendahara_jabatan") or f"Bendahara Pengeluaran {r['institution']}".strip()
    rows = [("Terima dari", [payer]),
            ("Uang sejumlah", [rp_id(s["total"]), f"({r['terbilang_id']})"]),
            ("Untuk keperluan", [r["purpose_id"]])]
    t = cell.add_table(rows=len(rows), cols=3)
    table_no_borders(t, zero_cell_margins=True)
    table_fixed(t, [34, 4, inner - 38])
    for row, (label, lines) in zip(t.rows, rows):
        keep_row_together(row)
        for c, txt in zip(row.cells[:2], (label, ":")):
            set_cell_margins(c, 50, 50, 0, 40)
            pp = c.paragraphs[0]
            para_spacing(pp, 0, 0, 1.3)
            d.run(pp, txt, size=10.5, color=BLACK)
        v = row.cells[2]
        set_cell_margins(v, 50, 50, 0, 0)
        for k, text in enumerate(lines):
            pp = v.paragraphs[0] if k == 0 else v.add_paragraph()
            pp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            para_spacing(pp, 0, 0, 1.3)
            d.run(pp, text, size=10.5, bold=label == "Uang sejumlah" and k == 0,
                  italic=label == "Uang sejumlah" and k == 1, color=BLACK)
    cell.paragraphs[0]._p.getparent().remove(cell.paragraphs[0]._p)
    d.spacer(10)
    # ---- tanda tangan: yang membayarkan (bendahara) & yang menerima (perusahaan)
    sig = d.doc.add_table(rows=1, cols=2)
    table_no_borders(sig, zero_cell_margins=True)
    half = d.content_w / 2
    table_fixed(sig, [half, half])
    keep_row_together(sig.rows[0])
    blocks = (
        ([_blank_date(r["doc_date"]), "Yang membayarkan,", ""],
         [("Nama", g.get("bendahara_nama") or "......................................"),
          ("Pangkat/NRP", g.get("bendahara_pangkat") or "-"), ("Jabatan", payer),
          ("NPWP", g.get("bendahara_npwp") or "-")]),
        ([_blank_date(r["doc_date"]), "Yang menerima,", d.b.legal_name],
         [("Nama", r["signer"] or "......................................"), ("Jabatan", r["signer_title"] or "-"),
          ("Alamat", d.b.address[:2]), ("NPWP", _company_npwp(d.b))]),
    )
    for c, (heads, info) in zip(sig.rows[0].cells, blocks):
        set_cell_margins(c, 0, 0, 0, 60)
        first = True
        for i, head in enumerate(heads):
            pp = c.paragraphs[0] if first else c.add_paragraph()
            first = False
            para_spacing(pp, 0, 0, 1.2)
            d.run(pp, head or " ", size=10, bold=i == 2, color=BLACK)
        gap = c.add_paragraph()
        para_spacing(gap, 0, 0, 1)
        gap.paragraph_format.space_before = Mm(30)  # ruang tanda tangan & meterai
        _kv_table(d, c, info, [24, 4, half - 31], size=9.5, first_bold=True)
    d.spacer(12)
    # ---- rincian potongan (BRUTO - potongan = NETO)
    rows = [("BRUTO", s["total"], True), ("PPN", s["ppn_collected"], False),
            (f"PPh 22 ({qty(r['pph22_rate'])}% × DPP barang {fmt_idr(s['dpp_goods'])})", s["pph22"], False),
            (f"PPh 23 ({qty(r['pph23_rate'])}% × DPP jasa {fmt_idr(s['dpp_services'])})", s["pph23"], False),
            ("Jumlah potongan", s["withheld"], False), ("NETO", s["neto"], True)]
    box = d.doc.add_table(rows=len(rows), cols=2)
    _grid(box, size=4, color="555555")
    table_fixed(box, [82, 38])
    box.alignment = 1
    for row, (label, value, bold) in zip(box.rows, rows):
        keep_row_together(row)
        for c, txt, al in ((row.cells[0], label, WD_ALIGN_PARAGRAPH.LEFT),
                           (row.cells[1], rp_id(value, cents=False) if value else "Rp -", WD_ALIGN_PARAGRAPH.RIGHT)):
            set_cell_margins(c, 45, 45, 80, 80)
            if bold:
                set_cell_shading(c, "EDEFF2")
            pp = c.paragraphs[0]
            pp.alignment = al
            para_spacing(pp, 0, 0, 1.1)
            d.run(pp, txt, size=9.5, bold=bold, color=BLACK)
    return d.save()


def build_gov_bast(r) -> bytes:
    d = LpjDoc(r["brand"])
    g = r["gov"]
    d.text("BERITA ACARA SERAH TERIMA PEKERJAAN", size=14, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER,
               after=0, underline=True)
    d.text(f"Nomor: {r['bast_no']}", size=10, align=WD_ALIGN_PARAGRAPH.CENTER, after=12, color=SOFT)
    day = r["doc_date"]
    d.text(f"Pada hari ini {HARI[day.weekday()]}, tanggal {tgl_id(day)}, bertempat di Jakarta, yang bertanda "
           "tangan di bawah ini:", after=6, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    d.fields([("1.  Nama", r["signer"] or "-"), ("     Jabatan", r["signer_title"] or "-"),
              ("     Perusahaan", d.b.legal_name)], widths=(38, 4, None), after=2)
    d.text("Selanjutnya disebut PIHAK PERTAMA.", italic=True, after=8)
    receiver_title = g.get("jabatan_klien") or "Pejabat Pembuat Komitmen"
    d.fields([("2.  Nama", r["client"]), ("     Jabatan", receiver_title),
              ("     Instansi", r["institution"] or "-")], widths=(38, 4, None), after=2)
    d.text("Selanjutnya disebut PIHAK KEDUA.", italic=True, after=10)
    basis = ""
    if g.get("dasar_nomor"):
        basis = f"Berdasarkan {g['dasar_nomor']}"
        if g.get("dasar_tanggal"):
            basis += f" tanggal {g['dasar_tanggal']}"
        basis += ", "
    work = f" {r['research_title']}" if r["research_title"] else ""
    d.text(f"{basis}PIHAK PERTAMA telah menyelesaikan dan menyerahkan pekerjaan{work} kepada PIHAK KEDUA, dan "
           "PIHAK KEDUA telah menerima hasil pekerjaan tersebut dalam keadaan baik dan lengkap, dengan rincian "
           "sebagai berikut:", after=6, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    rows = [[str(i), it["rincian"], "Barang" if it.get("jenis") == "BARANG" else "Jasa",
             f"{qty(it['banyaknya'])} {it['satuan']}",
             "-" if Decimal(it["volume"]) == 1 and it["satuan_volume"].lower() == "ls" else
             f"{qty(it['volume'])} {it['satuan_volume']}", "Baik / selesai"]
            for i, it in enumerate(r["items"], 1)]
    C = WD_ALIGN_PARAGRAPH.CENTER
    d.table(["No", "Uraian Pekerjaan / Barang", "Jenis", "Jumlah", "Volume", "Keterangan"], rows,
            [10, None, 18, 20, 20, 28], align={0: C, 2: C, 3: C, 4: C, 5: C})
    if r["activity_date"]:
        d.text(f"Pekerjaan dilaksanakan pada {r['activity_date']}.", after=6)
    d.text("Demikian Berita Acara Serah Terima ini dibuat dengan sebenarnya dalam rangkap 2 (dua), masing-masing "
           "mempunyai kekuatan hukum yang sama, untuk dipergunakan sebagaimana mestinya.", after=18,
           align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    d.two_signatures((["PIHAK KEDUA", "Yang menerima,", r["institution"] or ""], r["client"], receiver_title),
                     (["PIHAK PERTAMA", "Yang menyerahkan,", d.b.legal_name], r["signer"], r["signer_title"]))
    return d.save()
