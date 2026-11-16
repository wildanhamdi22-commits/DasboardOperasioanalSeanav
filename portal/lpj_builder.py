"""Template dokumen Pinjam Bendera (LPJ) bergaya surat resmi perusahaan Indonesia.

Kop surat bergaris ganda, judul di tengah, tabel bergaris penuh, terbilang, dan blok
"Hormat kami" — dipakai untuk Invoice, Kwitansi, dan Berita Acara Serah Terima.
Font mengikuti perusahaan (Seanav = Aptos, Oseana = Century Gothic).
"""
from __future__ import annotations

from decimal import Decimal

from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

from .doc_builder import (
    ASSET_DIR, BRANDS, DocBuilder, fmt_idr, keep_row_together, para_spacing, set_cell_margins,
    set_cell_shading, table_fixed, table_no_borders,
)

BLACK = "111111"
SOFT = "555555"
HEAD_FILL = "EDEFF2"
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
         "Oktober", "November", "Desember"]


def tgl(d):
    return f"{d.day} {BULAN[d.month - 1]} {d.year}" if d else "-"


def rp(value):
    return f"Rp {fmt_idr(value)},-"


def qty(value):
    value = Decimal(value)
    return f"{int(value)}" if value == value.to_integral() else f"{value.normalize():f}".replace(".", ",")


def _border(cell, **edges):
    """edges: top/bottom/left/right = (val, size_eighths, color) atau None (hapus)."""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge, spec in edges.items():
        el = OxmlElement(f"w:{edge}")
        if spec is None:
            el.set(qn("w:val"), "nil")
        else:
            el.set(qn("w:val"), spec[0])
            el.set(qn("w:sz"), str(spec[1]))
            el.set(qn("w:space"), "0")
            el.set(qn("w:color"), spec[2])
        borders.append(el)


def _grid(table, size=4, color="404040"):
    """Garis tabel penuh tipis."""
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "single")
        el.set(qn("w:sz"), str(size))
        el.set(qn("w:space"), "0")
        el.set(qn("w:color"), color)
        borders.append(el)
    tbl_pr.append(borders)


def _merge_row(row, start, end):
    cell = row.cells[start]
    for i in range(start + 1, end + 1):
        cell = cell.merge(row.cells[i])
    return cell


class LpjDoc(DocBuilder):
    """Dokumen berkop surat klasik."""

    def __init__(self, brand_code):
        super().__init__(BRANDS[brand_code])
        sec = self.doc.sections[0]
        sec.left_margin = sec.right_margin = Mm(20)
        sec.top_margin = Mm(12)
        sec.bottom_margin = Mm(18)
        self.content_w = 210 - 40
        self.doc.styles["Normal"].font.size = Pt(10)
        self.doc.styles["Normal"].font.color.rgb = None
        self._kop()

    # ---- kop surat
    def _kop(self):
        b = self.b
        t = self.doc.add_table(rows=1, cols=2)
        table_no_borders(t, zero_cell_margins=True)
        table_fixed(t, [48, self.content_w - 48])
        left, right = t.rows[0].cells
        left.vertical_alignment = right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        logo = ASSET_DIR / b.logo
        p = left.paragraphs[0]
        para_spacing(p, 0, 0, 1)
        if logo.exists():
            p.add_run().add_picture(str(logo), width=Mm(min(b.logo_width_mm, 44)))
        p = right.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        para_spacing(p, 0, 1, 1)
        self.run(p, b.legal_name, size=13, bold=True, color=b.primary, spacing=0.4)
        for line in [*b.address, *(c.strip() for c in b.contact.split("||"))]:
            q = right.add_paragraph()
            q.alignment = WD_ALIGN_PARAGRAPH.RIGHT
            para_spacing(q, 0, 0, 1.05)
            self.run(q, line, size=8, color=SOFT)
        rule = self.doc.add_paragraph()
        para_spacing(rule, 2, 10, 1)
        p_pr = rule._p.get_or_add_pPr()
        bdr = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "thinThickSmallGap")
        bottom.set(qn("w:sz"), "18")
        bottom.set(qn("w:space"), "1")
        bottom.set(qn("w:color"), BLACK)
        bdr.append(bottom)
        p_pr.append(bdr)

    # ---- elemen umum
    def text(self, value, size=10, bold=False, italic=False, align=None, before=0, after=4, color=BLACK,
             underline=False, line=1.2):
        p = self.doc.add_paragraph()
        para_spacing(p, before, after, line)
        if align:
            p.alignment = align
        r = self.run(p, value, size=size, bold=bold, italic=italic, color=color)
        r.font.underline = underline
        return p

    def title(self, title, number):
        p = self.text(title, size=15, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, after=0, underline=True)
        p.runs[0].font.all_caps = True
        self.text(f"Nomor: {number}", size=10, align=WD_ALIGN_PARAGRAPH.CENTER, after=12, color=SOFT)

    def fields(self, rows, widths=(40, 4, None), size=10, after=8):
        """Baris 'Label : isi' rata titik dua."""
        w = [widths[0], widths[1], widths[2] or self.content_w - widths[0] - widths[1]]
        t = self.doc.add_table(rows=len(rows), cols=3)
        table_no_borders(t, zero_cell_margins=True)
        table_fixed(t, w)
        for row, (label, value, opts) in zip(t.rows, [(r + ({},))[:3] for r in rows]):
            keep_row_together(row)
            for cell, content, bold in ((row.cells[0], label, False), (row.cells[1], ":" if label else "", False),
                                        (row.cells[2], value, opts.get("bold", False))):
                set_cell_margins(cell, 10, 10, 0, 0)
                p = cell.paragraphs[0]
                para_spacing(p, 0, 0, 1.2)
                lines = content if isinstance(content, list) else [content]
                for i, line in enumerate(lines):
                    q = p if i == 0 else cell.add_paragraph()
                    para_spacing(q, 0, 0, 1.2)
                    self.run(q, str(line or ""), size=size, bold=bold or opts.get("bold_all", False) and cell is row.cells[2],
                             italic=opts.get("italic", False) and cell is row.cells[2], color=BLACK)
        self.spacer(after)
        return t

    def table(self, columns, rows, widths, align=None, summary=()):
        """Tabel bergaris penuh. summary: [(label, nilai, bold)] digabung di kolom kiri."""
        n = len(columns)
        widths = [w if w else self.content_w - sum(x for x in widths if x) for w in widths]
        t = self.doc.add_table(rows=1 + len(rows) + len(summary), cols=n)
        _grid(t)
        table_fixed(t, widths)
        align = align or {}
        hdr = t.rows[0]
        keep_row_together(hdr)
        for i, col in enumerate(columns):
            c = hdr.cells[i]
            set_cell_shading(c, HEAD_FILL)
            set_cell_margins(c, 70, 70, 80, 80)
            c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            p = c.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            para_spacing(p, 0, 0, 1.1)
            self.run(p, col, size=9, bold=True, color=BLACK)
        for r_idx, values in enumerate(rows, 1):
            row = t.rows[r_idx]
            keep_row_together(row)
            for i, value in enumerate(values):
                c = row.cells[i]
                set_cell_margins(c, 60, 60, 80, 80)
                c.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
                lines = value if isinstance(value, list) else [value]
                for k, line in enumerate(lines):
                    p = c.paragraphs[0] if k == 0 else c.add_paragraph()
                    para_spacing(p, 0, 0, 1.15)
                    p.alignment = align.get(i, WD_ALIGN_PARAGRAPH.LEFT)
                    self.run(p, str(line), size=9.5 if k == 0 else 8.5, color=BLACK if k == 0 else SOFT,
                             italic=k > 0)
        for s_idx, (label, value, bold) in enumerate(summary):
            row = t.rows[1 + len(rows) + s_idx]
            keep_row_together(row)
            left = _merge_row(row, 0, n - 2)
            right = row.cells[n - 1]
            for c, content, al in ((left, label, WD_ALIGN_PARAGRAPH.RIGHT), (right, value, WD_ALIGN_PARAGRAPH.RIGHT)):
                set_cell_margins(c, 55, 55, 80, 80)
                if bold:
                    set_cell_shading(c, HEAD_FILL)
                p = c.paragraphs[0]
                p.alignment = al
                para_spacing(p, 0, 0, 1.1)
                self.run(p, content, size=9.5 if not bold else 10, bold=bold, color=BLACK)
        self.spacer(8)
        return t

    def boxed(self, label, value, italic=True):
        t = self.doc.add_table(rows=1, cols=1)
        table_fixed(t, [self.content_w])
        c = t.rows[0].cells[0]
        _border(c, top=("single", 4, "404040"), bottom=("single", 4, "404040"),
                left=("single", 4, "404040"), right=("single", 4, "404040"))
        set_cell_shading(c, "F7F8FA")
        set_cell_margins(c, 90, 90, 120, 120)
        p = c.paragraphs[0]
        para_spacing(p, 0, 0, 1.2)
        self.run(p, f"{label}: ", size=9.5, bold=True, color=BLACK)
        self.run(p, value, size=10, italic=italic, bold=True, color=BLACK)
        self.spacer(10)

    def signature(self, place_date, opening, company, name, title, height_mm=24, note=None, col_mm=70):
        """Blok tanda tangan kanan: tempat/tanggal, salam, perusahaan, ruang ttd, nama (garis bawah), jabatan."""
        t = self.doc.add_table(rows=1, cols=2)
        table_no_borders(t, zero_cell_margins=True)
        table_fixed(t, [self.content_w - col_mm, col_mm])
        keep_row_together(t.rows[0])
        c = t.rows[0].cells[1]
        self._sign_block(c, [place_date, opening, company], name, title, height_mm, note)
        return t

    def two_signatures(self, left, right, height_mm=24):
        t = self.doc.add_table(rows=1, cols=2)
        table_no_borders(t, zero_cell_margins=True)
        half = self.content_w / 2
        table_fixed(t, [half, half])
        keep_row_together(t.rows[0])
        for cell, (heads, name, title) in zip(t.rows[0].cells, (left, right)):
            self._sign_block(cell, heads, name, title, height_mm, None, center=True)

    def _sign_block(self, cell, heads, name, title, height_mm, note, center=False):
        align = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.LEFT
        first = True
        for i, head in enumerate(h for h in heads if h):
            p = cell.paragraphs[0] if first else cell.add_paragraph()
            first = False
            p.alignment = align
            para_spacing(p, 0, 0, 1.2)
            self.run(p, head, size=10, bold=i == len([h for h in heads if h]) - 1 and not center, color=BLACK)
        gap = cell.add_paragraph()
        gap.alignment = align
        para_spacing(gap, 0, 0, 1)
        gap.paragraph_format.space_before = Mm(height_mm - 6)
        if note:
            self.run(gap, note, size=7.5, color="9AA1AB", italic=True)
        p = cell.add_paragraph()
        p.alignment = align
        para_spacing(p, 4, 0, 1.2)
        r = self.run(p, name or "(......................................)", size=10, bold=True, color=BLACK)
        r.font.underline = bool(name)
        if title:
            q = cell.add_paragraph()
            q.alignment = align
            para_spacing(q, 0, 0, 1.2)
            self.run(q, title, size=9.5, color=BLACK)


# --------------------------------------------------------------------------- dokumen
def _uraian(item):
    return item["rincian"]


def build_lpj_invoice(r) -> bytes:
    d = LpjDoc(r["brand"])
    d.title("INVOICE", r["invoice_no"])
    t = d.doc.add_table(rows=1, cols=2)
    table_no_borders(t, zero_cell_margins=True)
    table_fixed(t, [d.content_w * 0.55, d.content_w * 0.45])
    left, right = t.rows[0].cells
    p = left.paragraphs[0]
    para_spacing(p, 0, 0, 1.25)
    d.run(p, "Kepada Yth.", size=10, color=BLACK)
    for i, line in enumerate([r["client"], r["institution"], *r["address_lines"]]):
        if not line:
            continue
        q = left.add_paragraph()
        para_spacing(q, 0, 0, 1.25)
        d.run(q, line, size=10, bold=i == 0, color=BLACK)
    meta = [("Tanggal", tgl(r["doc_date"])), ("No. Referensi", r["number"]), ("Jatuh tempo", tgl(r["doc_date"]))]
    mt = right.add_table(rows=len(meta), cols=3)
    table_no_borders(mt, zero_cell_margins=True)
    table_fixed(mt, [30, 4, d.content_w * 0.45 - 34])
    for row, (k, v) in zip(mt.rows, meta):
        for c, txt in zip(row.cells, (k, ":", v)):
            q = c.paragraphs[0]
            para_spacing(q, 0, 0, 1.25)
            d.run(q, txt, size=10, color=BLACK)
    right.paragraphs[0]._p.getparent().remove(right.paragraphs[0]._p)
    d.spacer(10)
    if r["research_title"]:
        d.fields([("Perihal", f"Jasa / sewa peralatan untuk penelitian “{r['research_title']}”")], widths=(32, 4, None), after=2)
    if r["activity_date"]:
        d.fields([("Waktu kegiatan", r["activity_date"])], widths=(32, 4, None), after=8)
    rows = []
    for i, item in enumerate(r["items"], 1):
        rows.append([str(i), _uraian(item), f"{qty(item['banyaknya'])} {item['satuan']}",
                     f"{qty(item['volume'])} {item['satuan_volume']}", fmt_idr(item["harga_satuan"]),
                     fmt_idr(item["jumlah"])])
    summary = []
    if r["include_tax"]:
        summary += [("Jumlah (termasuk PPN)", fmt_idr(r["total"]), False),
                    ("Dasar Pengenaan Pajak (DPP)", fmt_idr(r["dpp"]), False),
                    (f"PPN {qty(r['tax_rate'])}%", fmt_idr(r["ppn"]), False)]
    else:
        summary += [("Jumlah (DPP)", fmt_idr(r["dpp"]), False), (f"PPN {qty(r['tax_rate'])}%", fmt_idr(r["ppn"]), False)]
    summary.append(("TOTAL TAGIHAN", fmt_idr(r["total"]), True))
    R, C = WD_ALIGN_PARAGRAPH.RIGHT, WD_ALIGN_PARAGRAPH.CENTER
    d.table(["No", "Uraian", "Banyaknya", "Volume", "Harga Satuan (Rp)", "Jumlah (Rp)"], rows,
            [10, None, 22, 22, 30, 30], align={0: C, 2: C, 3: C, 4: R, 5: R}, summary=summary)
    d.boxed("Terbilang", r["terbilang"])
    d.text("Pembayaran mohon ditransfer ke rekening berikut:", size=9.5, after=3)
    bank = d.b.bank
    d.fields([("Bank", bank[0] if bank else "-"), ("No. Rekening", bank[2] if len(bank) > 2 else "-"),
              ("Atas nama", bank[3].replace("a.n. ", "") if len(bank) > 3 else d.b.legal_name)],
             widths=(30, 4, None), size=9.5, after=14)
    d.signature(f"Jakarta, {tgl(r['doc_date'])}", "Hormat kami,", d.b.legal_name, r["signer"], r["signer_title"],
                height_mm=20)
    return d.save()


def build_lpj_receipt(r) -> bytes:
    d = LpjDoc(r["brand"])
    outer = d.doc.add_table(rows=1, cols=1)
    table_fixed(outer, [d.content_w])
    cell = outer.rows[0].cells[0]
    dbl = ("double", 6, "222222")
    _border(cell, top=dbl, bottom=dbl, left=dbl, right=dbl)
    set_cell_margins(cell, 160, 160, 220, 220)
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para_spacing(p, 0, 0, 1.1)
    title = d.run(p, "KWITANSI", size=18, bold=True, color=BLACK, spacing=3)
    title.font.underline = True
    q = cell.add_paragraph()
    q.alignment = WD_ALIGN_PARAGRAPH.CENTER
    para_spacing(q, 2, 10, 1.1)
    d.run(q, f"Nomor: {r['receipt_no']}", size=10, color=SOFT)
    inner_w = d.content_w - 8
    t = cell.add_table(rows=3, cols=3)
    table_no_borders(t, zero_cell_margins=True)
    table_fixed(t, [40, 5, inner_w - 45])
    rows = [("Sudah terima dari", [r["client"], r["institution"]] if r["institution"] else [r["client"]], False),
            ("Uang sejumlah", [r["terbilang"]], True),
            ("Untuk pembayaran", [r["purpose"]], False)]
    for row, (label, lines, shade) in zip(t.rows, rows):
        keep_row_together(row)
        for c, content in zip(row.cells, (label, ":", None)):
            set_cell_margins(c, 70, 70, 0, 60)
            if content is not None:
                p = c.paragraphs[0]
                para_spacing(p, 0, 0, 1.3)
                d.run(p, content, size=10.5, color=BLACK)
        v = row.cells[2]
        if shade:
            set_cell_shading(v, "EEF0F3")
            set_cell_margins(v, 70, 70, 90, 90)
        for i, line in enumerate(lines):
            p = v.paragraphs[0] if i == 0 else v.add_paragraph()
            para_spacing(p, 0, 0, 1.3)
            d.run(p, line, size=10.5, bold=shade or i == 0 and label == "Sudah terima dari", italic=shade, color=BLACK)
    gap = cell.add_paragraph()
    para_spacing(gap, 0, 8, 1)
    bottom = cell.add_table(rows=1, cols=2)
    table_no_borders(bottom, zero_cell_margins=True)
    table_fixed(bottom, [inner_w - 72, 72])
    left, right = bottom.rows[0].cells
    left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
    amount = left.add_table(rows=1, cols=1)
    table_fixed(amount, [62])
    ac = amount.rows[0].cells[0]
    thick = ("single", 12, "222222")
    _border(ac, top=thick, bottom=thick, left=thick, right=thick)
    set_cell_shading(ac, "F4F5F7")
    set_cell_margins(ac, 110, 110, 140, 140)
    ap = ac.paragraphs[0]
    para_spacing(ap, 0, 0, 1)
    d.run(ap, rp(r["total"]), size=14, bold=True, color=BLACK)
    left.paragraphs[0]._p.getparent().remove(left.paragraphs[0]._p)
    d._sign_block(right, [f"Jakarta, {tgl(r['doc_date'])}", "Yang menerima,", d.b.legal_name], r["signer"],
                  r["signer_title"], 26, "Materai Rp10.000")
    return d.save()


def build_lpj_bast(r) -> bytes:
    d = LpjDoc(r["brand"])
    d.title("BERITA ACARA SERAH TERIMA", r["bast_no"])
    day = r["doc_date"]
    d.text(f"Pada hari ini, {HARI[day.weekday()]} tanggal {tgl(day)}, kami yang bertanda tangan di bawah ini:",
           after=6)
    d.fields([("1.  Nama", r["signer"] or "-"), ("     Jabatan", r["signer_title"] or "-"),
              ("     Perusahaan", d.b.legal_name)], widths=(38, 4, None), after=2)
    d.text("Selanjutnya disebut sebagai PIHAK PERTAMA.", italic=True, after=8)
    d.fields([("2.  Nama", r["client"]), ("     Jabatan", "Ketua Peneliti"),
              ("     Instansi", r["institution"] or "-")], widths=(38, 4, None), after=2)
    d.text("Selanjutnya disebut sebagai PIHAK KEDUA.", italic=True, after=10)
    opening = ("PIHAK PERTAMA telah menyerahkan kepada PIHAK KEDUA, dan PIHAK KEDUA telah menerima, "
               "pekerjaan / penggunaan peralatan dengan rincian sebagai berikut:")
    d.text(opening, after=6, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    rows = [[str(i), _uraian(it), f"{qty(it['banyaknya'])} {it['satuan']}",
             f"{qty(it['volume'])} {it['satuan_volume']}", "Baik, selesai"] for i, it in enumerate(r["items"], 1)]
    C = WD_ALIGN_PARAGRAPH.CENTER
    d.table(["No", "Uraian Pekerjaan / Peralatan", "Banyaknya", "Volume", "Keterangan"], rows,
            [10, None, 24, 24, 30], align={0: C, 2: C, 3: C, 4: C})
    extra = []
    if r["research_title"]:
        extra.append(f"dalam rangka kegiatan penelitian “{r['research_title']}”")
    if r["activity_date"]:
        extra.append(f"yang dilaksanakan pada {r['activity_date']}")
    if extra:
        d.text("Pekerjaan tersebut dilaksanakan " + ", ".join(extra) + ", dalam keadaan baik dan lengkap.",
               after=6, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    d.text("Demikian Berita Acara Serah Terima ini dibuat dengan sebenarnya dalam rangkap 2 (dua) untuk "
           "dipergunakan sebagaimana mestinya.", after=18, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    d.two_signatures((["PIHAK PERTAMA", d.b.legal_name], r["signer"], r["signer_title"]),
                     (["PIHAK KEDUA", r["institution"] or "Ketua Peneliti"], r["client"], "Ketua Peneliti"))
    return d.save()
