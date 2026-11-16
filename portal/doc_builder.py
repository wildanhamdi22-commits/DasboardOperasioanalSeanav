"""Generator dokumen resmi (Quotation, Invoice, Delivery Order) dengan satu gaya
untuk PT Oseana Prawira Persada (font Century Gothic) dan PT Seanav Maritim
Indonesia (font Aptos).

Semua tata letak dibangun lewat python-docx sehingga ketiga jenis dokumen dan
kedua perusahaan selalu konsisten. Data dokumen dikirim sebagai dict sederhana
(lihat SAMPLE_* di bagian bawah file).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

ASSET_DIR = Path(__file__).resolve().parent / "doc_assets"


# --------------------------------------------------------------------------- brand
@dataclass
class Brand:
    code: str
    legal_name: str
    font: str
    primary: str          # warna utama (hex tanpa #)
    accent: str           # warna aksen
    tint: str             # latar lembut untuk baris grup / kotak
    logo: str             # nama file di doc_assets
    logo_width_mm: float
    address: list[str] = field(default_factory=list)
    contact: str = ""
    bank: list[str] = field(default_factory=list)
    tagline: str = ""


BRANDS = {
    "QR": Brand(
        code="QR",
        legal_name="PT OSEANA PRAWIRA PERSADA",
        font="Century Gothic",
        primary="17394A",
        accent="21A3B5",
        tint="EAF5F7",
        logo="oseana-logo.png",
        logo_width_mm=52,
        address=["Indonesia Stock Exchange Tower II, 17th Floor",
                 "Jl. Jend. Sudirman Kav. 52-53, Jakarta Selatan, Indonesia 12190"],
        contact="Tel: +62 21 5291 7489  ·  Fax: +62 21 5157799 || NPWP: 86.208.779.8-012.000 || sales@oseanaprawirapersada.com  ·  www.oseanaprawirapersada.com",
        bank=["BANK MANDIRI — KCP Jakarta Bursa Efek",
              "Gd. Bursa Efek Jakarta Tower II, Lt. 17",
              "A/C 104-00-017-01189 (IDR)  ·  A/C 104-000-592-9042 (USD)",
              "a.n. PT OSEANA PRAWIRA PERSADA"],
        tagline="Marine Survey & Operations",
    ),
    "SNV": Brand(
        code="SNV",
        legal_name="PT SEANAV MARITIM INDONESIA",
        font="Aptos",
        primary="0B2A6F",
        accent="D7262E",
        tint="EEF2FA",
        logo="seanav-logo.png",
        logo_width_mm=40,
        address=["Sahid Sudirman Center, 56th Floor",
                 "Jl. Jenderal Sudirman No. 86, Jakarta 10220, Indonesia"],
        contact="Tel: +62 21 5799 8020  ·  Fax: +62 21 8063 1999 || NPWP: 1000 0000 0711 8487 || sales@seanavgroup.co.id  ·  www.seanavgroup.co.id",
        bank=["BANK MANDIRI — Cabang Jakarta Sahardjo",
              "Komplek Gajah Unit F&G, Jl. Dr. Saharjo No. 111, Jakarta Selatan 12810",
              "A/C 1240016082928 (IDR)",
              "a.n. PT SEANAV MARITIM INDONESIA"],
        tagline="Navigate the Future",
    ),
}

GREY = "5B6675"
LINE = "D5DAE1"
INK = "1C2430"


# --------------------------------------------------------------------------- helpers
def rgb(hex_):
    return RGBColor.from_string(hex_)


def fmt_idr(value) -> str:
    value = Decimal(value or 0).quantize(Decimal("1"))
    return f"{value:,.0f}".replace(",", ".")


def fmt_qty(value) -> str:
    value = Decimal(value)
    return f"{value.normalize():f}" if value != value.to_integral() else f"{int(value)}"


_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
         "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
         "eighteen", "nineteen"]
_TENS = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]


def _words_999(n: int) -> str:
    parts = []
    if n >= 100:
        parts.append(_ONES[n // 100] + " hundred")
        n %= 100
    if n >= 20:
        parts.append(_TENS[n // 10] + ("-" + _ONES[n % 10] if n % 10 else ""))
    elif n:
        parts.append(_ONES[n])
    return " ".join(parts)


def amount_in_words(value) -> str:
    n = int(Decimal(value or 0).quantize(Decimal("1")))
    if n == 0:
        return "Zero Rupiah"
    chunks = []
    for size, name in ((10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (10**3, "thousand"), (1, "")):
        if n >= size:
            chunks.append((_words_999(n // size) + (" " + name if name else "")).strip())
            n %= size
    text = ", ".join(chunks)
    return text[0].upper() + text[1:] + " Rupiah"


def set_cell_shading(cell, hex_):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_)
    tc_pr.append(shd)


def set_cell_borders(cell, **edges):
    """edges: top/bottom/left/right = (size_eighths_pt, hex) or None to clear."""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for edge in ("top", "left", "bottom", "right"):
        if edge not in edges:
            continue
        el = OxmlElement(f"w:{edge}")
        spec = edges[edge]
        if spec is None:
            el.set(qn("w:val"), "nil")
        else:
            el.set(qn("w:val"), "single")
            el.set(qn("w:sz"), str(spec[0]))
            el.set(qn("w:color"), spec[1])
            el.set(qn("w:space"), "0")
        borders.append(el)


def set_cell_margins(cell, top=60, bottom=60, left=100, right=100):
    tc_pr = cell._tc.get_or_add_tcPr()
    mar = OxmlElement("w:tcMar")
    for name, val in (("top", top), ("bottom", bottom), ("start", left), ("end", right)):
        el = OxmlElement(f"w:{name}")
        el.set(qn("w:w"), str(val))
        el.set(qn("w:type"), "dxa")
        mar.append(el)
    tc_pr.append(mar)


def table_no_borders(table, zero_cell_margins=False):
    tbl_pr = table._tbl.tblPr
    ind = OxmlElement("w:tblInd")
    ind.set(qn("w:w"), "0")
    ind.set(qn("w:type"), "dxa")
    tbl_pr.append(ind)
    if zero_cell_margins:
        mar = OxmlElement("w:tblCellMar")
        for edge in ("top", "left", "bottom", "right"):
            el = OxmlElement(f"w:{edge}")
            el.set(qn("w:w"), "0")
            el.set(qn("w:type"), "dxa")
            mar.append(el)
        tbl_pr.append(mar)
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{edge}")
        el.set(qn("w:val"), "nil")
        borders.append(el)
    tbl_pr.append(borders)


def table_fixed(table, widths_mm):
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tbl_pr.append(layout)
    grid = table._tbl.tblGrid
    for col, w in zip(grid.findall(qn("w:gridCol")), widths_mm):
        col.set(qn("w:w"), str(int(Mm(w).twips)))
    for row in table.rows:
        for cell, w in zip(row.cells, widths_mm):
            cell.width = Mm(w)


def keep_row_together(row):
    tr_pr = row._tr.get_or_add_trPr()
    cant = OxmlElement("w:cantSplit")
    tr_pr.append(cant)


def repeat_header(row):
    tr_pr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    tr_pr.append(el)


def para_spacing(p, before=0, after=0, line=1.0):
    pf = p.paragraph_format
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = line


def para_bottom_border(p, size=12, color="000000", space=4):
    p_pr = p._p.get_or_add_pPr()
    bdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), str(space))
    bottom.set(qn("w:color"), color)
    bdr.append(bottom)
    p_pr.append(bdr)


def add_field(run, instr):
    for kind, text in (("begin", None), ("instr", instr), ("separate", None), ("text", "1"), ("end", None)):
        if kind == "instr":
            el = OxmlElement("w:instrText")
            el.set(qn("xml:space"), "preserve")
            el.text = f" {text} "
        elif kind == "text":
            el = OxmlElement("w:t")
            el.text = text
        else:
            el = OxmlElement("w:fldChar")
            el.set(qn("w:fldCharType"), kind)
        run._r.append(el)


# --------------------------------------------------------------------------- builder
class DocBuilder:
    def __init__(self, brand: Brand):
        self.b = brand
        self.doc = Document()
        self._setup()

    # ---- base styles
    def _set_compat_15(self):
        """Mode kompatibilitas Word 2013+: tepi tabel rata dengan margin halaman,
        sehingga tabel, paragraf, dan tanda tangan berada di garis margin yang sama."""
        settings = self.doc.settings.element
        compat = settings.find(qn("w:compat"))
        if compat is None:
            compat = OxmlElement("w:compat")
            settings.append(compat)
        for el in compat.findall(qn("w:compatSetting")):
            if el.get(qn("w:name")) == "compatibilityMode":
                compat.remove(el)
        cs = OxmlElement("w:compatSetting")
        cs.set(qn("w:name"), "compatibilityMode")
        cs.set(qn("w:uri"), "http://schemas.microsoft.com/office/word")
        cs.set(qn("w:val"), "15")
        compat.append(cs)

    def _setup(self):
        self._set_compat_15()
        sec = self.doc.sections[0]
        sec.page_width, sec.page_height = Mm(210), Mm(297)
        sec.left_margin = sec.right_margin = Mm(16)
        sec.top_margin = Mm(14)
        sec.bottom_margin = Mm(16)
        sec.header_distance = Mm(8)
        sec.footer_distance = Mm(8)
        self.content_w = 210 - 32
        normal = self.doc.styles["Normal"]
        normal.font.name = self.b.font
        normal.font.size = Pt(9)
        normal.font.color.rgb = rgb(INK)
        rpr = normal.element.get_or_add_rPr()
        fonts = rpr.find(qn("w:rFonts"))
        if fonts is None:
            fonts = OxmlElement("w:rFonts")
            rpr.append(fonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            fonts.set(qn(attr), self.b.font)
        para_spacing(normal, 0, 0, 1.08) if False else None
        normal.paragraph_format.space_after = Pt(0)
        normal.paragraph_format.space_before = Pt(0)
        normal.paragraph_format.line_spacing = 1.1
        # dokumen default (docDefaults) juga diset agar teks tabel ikut font brand
        styles = self.doc.styles.element
        rpr_default = styles.find(qn("w:docDefaults")).find(qn("w:rPrDefault")).find(qn("w:rPr"))
        rf = rpr_default.find(qn("w:rFonts"))
        if rf is None:
            rf = OxmlElement("w:rFonts")
            rpr_default.append(rf)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rf.set(qn(attr), self.b.font)
        for theme_attr in ("w:asciiTheme", "w:hAnsiTheme", "w:cstheme", "w:eastAsiaTheme"):
            if rf.get(qn(theme_attr)) is not None:
                del rf.attrib[qn(theme_attr)]

    def run(self, p, text, size=None, bold=False, color=None, italic=False, caps=False, spacing=None):
        r = p.add_run(text)
        if size:
            r.font.size = Pt(size)
        r.font.bold = bold
        r.font.italic = italic
        r.font.all_caps = caps
        if color:
            r.font.color.rgb = rgb(color)
        if spacing is not None:
            rpr = r._r.get_or_add_rPr()
            sp = OxmlElement("w:spacing")
            sp.set(qn("w:val"), str(int(spacing * 20)))
            rpr.append(sp)
        return r

    def para(self, container=None, align=None, before=0, after=0, line=1.1):
        container = container or self.doc
        p = container.add_paragraph()
        para_spacing(p, before, after, line)
        if align:
            p.alignment = align
        return p

    def cell_text(self, cell, lines, align=None, size=9, first_bold=False, color=None, first_color=None):
        cell.paragraphs[0].text = ""
        for i, line in enumerate(lines):
            p = cell.paragraphs[0] if i == 0 else cell.add_paragraph()
            para_spacing(p, 0, 0, 1.1)
            if align:
                p.alignment = align
            bold = first_bold and i == 0
            col = first_color if (i == 0 and first_color) else color
            if isinstance(line, tuple):
                text, opts = line
                self.run(p, text, size=opts.get("size", size), bold=opts.get("bold", bold),
                         color=opts.get("color", col), italic=opts.get("italic", False),
                         caps=opts.get("caps", False), spacing=opts.get("spacing"))
            else:
                self.run(p, line, size=size, bold=bold, color=col)
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP

    # ---- header & footer (berulang di setiap halaman)
    def header_footer(self, doc_title, doc_number):
        sec = self.doc.sections[0]
        header = sec.header
        header.is_linked_to_previous = False
        t = header.add_table(rows=1, cols=2, width=Mm(self.content_w))
        table_no_borders(t, zero_cell_margins=True)
        table_fixed(t, [62, self.content_w - 62])
        left, right = t.rows[0].cells
        set_cell_margins(left, 0, 0, 0, 0)
        set_cell_margins(right, 0, 0, 0, 0)
        left.paragraphs[0].text = ""
        logo = ASSET_DIR / self.b.logo
        if logo.exists():
            left.paragraphs[0].add_run().add_picture(str(logo), width=Mm(self.b.logo_width_mm))
        left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        lines = [(self.b.legal_name, {"bold": True, "size": 10, "color": self.b.primary, "spacing": 0.6})]
        lines += [(a, {"size": 7, "color": GREY}) for a in self.b.address]
        lines += [(c.strip(), {"size": 7, "color": GREY}) for c in self.b.contact.split("||")]
        self.cell_text(right, lines, align=WD_ALIGN_PARAGRAPH.RIGHT)
        right.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        # garis brand: tebal warna utama + tipis aksen
        p = header.paragraphs[0] if header.paragraphs and not header.paragraphs[0].text else header.add_paragraph()
        bar = header.add_table(rows=1, cols=2, width=Mm(self.content_w))
        table_no_borders(bar, zero_cell_margins=True)
        table_fixed(bar, [self.content_w - 34, 34])
        for cell, color in zip(bar.rows[0].cells, (self.b.primary, self.b.accent)):
            set_cell_shading(cell, color)
            set_cell_margins(cell, 0, 0, 0, 0)
            cp = cell.paragraphs[0]
            para_spacing(cp, 0, 0, 1)
            r = cp.add_run(" ")
            r.font.size = Pt(2.5)
        tr_pr = bar.rows[0]._tr.get_or_add_trPr()
        h = OxmlElement("w:trHeight")
        h.set(qn("w:val"), str(int(Mm(1.3).twips)))
        h.set(qn("w:hRule"), "exact")
        tr_pr.append(h)
        # hapus paragraf kosong bawaan di atas tabel header
        first = header.paragraphs[0]
        if not first.text and first._p.getparent() is not None and first._p is header._element[0]:
            header._element.remove(first._p)
        after = header.add_paragraph()
        para_spacing(after, 0, 0, 1)
        after.add_run().font.size = Pt(2)

        footer = sec.footer
        ft = footer.add_table(rows=1, cols=2, width=Mm(self.content_w))
        table_no_borders(ft, zero_cell_margins=True)
        table_fixed(ft, [self.content_w - 40, 40])
        fl, fr = ft.rows[0].cells
        for c in (fl, fr):
            set_cell_borders(c, top=(6, LINE))
        self.cell_text(fl, [(f"{self.b.legal_name.title().replace('Pt ', 'PT ')}  ·  {doc_title}  {doc_number}",
                             {"size": 7, "color": GREY})])
        fp = fr.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        self.run(fp, "Page ", size=7, color=GREY)
        add_field(self.run(fp, "", size=7, color=GREY), "PAGE")
        self.run(fp, " of ", size=7, color=GREY)
        add_field(self.run(fp, "", size=7, color=GREY), "NUMPAGES")
        if footer.paragraphs and not footer.paragraphs[0].text and footer.paragraphs[0]._p is footer._element[0]:
            footer._element.remove(footer.paragraphs[0]._p)

    # ---- judul + kotak meta
    def title_block(self, title, subtitle, meta):
        t = self.doc.add_table(rows=1, cols=2)
        table_no_borders(t)
        table_fixed(t, [92, self.content_w - 92])
        left, right = t.rows[0].cells
        self.cell_text(left, [
            (title, {"bold": True, "size": 22, "color": self.b.primary, "spacing": 2.5}),
            (subtitle, {"size": 8.5, "color": self.b.accent, "bold": True, "spacing": 1.2, "caps": True}),
        ])
        left.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        # meta: tabel kecil di dalam sel kanan
        set_cell_margins(right, 0, 0, 0, 0)
        set_cell_margins(left, 0, 0, 0, 0)
        right.paragraphs[0].text = ""
        inner = right.add_table(rows=len(meta), cols=2)
        table_no_borders(inner)
        table_fixed(inner, [30, self.content_w - 92 - 30])
        inner.alignment = WD_TABLE_ALIGNMENT.RIGHT
        for i, (label, value) in enumerate(meta):
            lc, vc = inner.rows[i].cells
            for c in (lc, vc):
                set_cell_margins(c, 35, 35, 90, 90)
                set_cell_borders(c, bottom=(4, LINE))
            set_cell_shading(lc, self.b.tint)
            self.cell_text(lc, [(label.upper(), {"size": 7, "bold": True, "color": self.b.primary, "spacing": 0.6})])
            bold = i == 0
            self.cell_text(vc, [(value, {"size": 9 if not bold else 9.5, "bold": bold})], align=WD_ALIGN_PARAGRAPH.RIGHT)
        # paragraf kosong wajib di akhir sel → kecilkan
        last = right.paragraphs[-1]
        para_spacing(last, 0, 0, 1)
        if right.paragraphs[0].text == "" and len(right.paragraphs) > 1:
            right._tc.remove(right.paragraphs[0]._p)
        self.spacer(8)

    def spacer(self, pt=6):
        p = self.para()
        p.paragraph_format.line_spacing = Pt(pt)
        r = p.add_run("")
        r.font.size = Pt(1)
        return p

    def section_label(self, cell_or_doc, text):
        pass

    # ---- kotak pihak (Bill to / Ship to / ...)
    def party_boxes(self, boxes):
        n = len(boxes)
        gap = 4
        w = (self.content_w - gap * (n - 1)) / n
        t = self.doc.add_table(rows=1, cols=n * 2 - 1)
        table_no_borders(t)
        widths = []
        for i in range(n):
            widths.append(w)
            if i < n - 1:
                widths.append(gap)
        table_fixed(t, widths)
        cells = t.rows[0].cells
        for i, (label, lines) in enumerate(boxes):
            c = cells[i * 2]
            set_cell_margins(c, 90, 100, 120, 120)
            set_cell_borders(c, top=(18, self.b.primary), bottom=(4, LINE), left=(4, LINE), right=(4, LINE))
            content = [(label.upper(), {"size": 7, "bold": True, "color": self.b.accent, "spacing": 1})]
            lines = [x for x in lines if x and str(x).strip()]
            for j, line in enumerate(lines):
                if j == 0:
                    content.append((line, {"size": 9.5, "bold": True, "color": INK}))
                else:
                    content.append((line, {"size": 8.5, "color": GREY}))
            self.cell_text(c, content)
        self.spacer(8)

    # ---- tabel item
    def items_table(self, columns, rows, widths, numeric_cols=(), center_cols=()):
        t = self.doc.add_table(rows=1, cols=len(columns))
        table_no_borders(t)
        table_fixed(t, widths)
        hdr = t.rows[0]
        repeat_header(hdr)
        for i, (cell, label) in enumerate(zip(hdr.cells, columns)):
            set_cell_shading(cell, self.b.primary)
            set_cell_margins(cell, 70, 70, 90, 90)
            align = WD_ALIGN_PARAGRAPH.RIGHT if i in numeric_cols else (WD_ALIGN_PARAGRAPH.CENTER if i in center_cols else None)
            self.cell_text(cell, [(label.upper(), {"size": 7.5, "bold": True, "color": "FFFFFF", "spacing": 0.6})], align=align)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        zebra = False
        for row in rows:
            r = t.add_row()
            keep_row_together(r)
            if row.get("group"):
                merged = r.cells[0].merge(r.cells[-1])
                set_cell_shading(merged, self.b.tint)
                set_cell_margins(merged, 55, 55, 90, 90)
                set_cell_borders(merged, bottom=(4, LINE))
                self.cell_text(merged, [(row["group"].upper(), {"size": 7.5, "bold": True, "color": self.b.primary, "spacing": 1})])
                zebra = False
                continue
            for i, (cell, value) in enumerate(zip(r.cells, row["cells"])):
                set_cell_margins(cell, 70, 70, 90, 90)
                set_cell_borders(cell, bottom=(4, LINE))
                if zebra:
                    set_cell_shading(cell, "FAFBFC")
                align = WD_ALIGN_PARAGRAPH.RIGHT if i in numeric_cols else (WD_ALIGN_PARAGRAPH.CENTER if i in center_cols else None)
                if isinstance(value, list):
                    lines = [(value[0], {"bold": True, "size": 9})] + [(v, {"size": 8, "color": GREY}) for v in value[1:]]
                    self.cell_text(cell, lines, align=align)
                else:
                    self.cell_text(cell, [(str(value), {"size": 9})], align=align)
            zebra = not zebra
        return t

    # ---- ringkasan total + catatan kiri
    def totals(self, left_lines, rows):
        self.spacer(6)
        t = self.doc.add_table(rows=1, cols=3)
        table_no_borders(t)
        table_fixed(t, [self.content_w - 80, 6, 74])
        left, _, right = t.rows[0].cells
        set_cell_margins(left, 0, 0, 0, 0)
        set_cell_margins(right, 0, 0, 0, 0)
        self.cell_text(left, left_lines or [""])
        right.paragraphs[0].text = ""
        inner = right.add_table(rows=len(rows), cols=2)
        table_no_borders(inner)
        table_fixed(inner, [40, 34])
        for i, (label, value, strong) in enumerate(rows):
            lc, vc = inner.rows[i].cells
            for c in (lc, vc):
                set_cell_margins(c, 55, 55, 90, 90)
                if strong:
                    set_cell_shading(c, self.b.primary)
                else:
                    set_cell_borders(c, bottom=(4, LINE))
            color = "FFFFFF" if strong else INK
            self.cell_text(lc, [(label, {"size": 8 if not strong else 8.5, "bold": strong, "color": color if strong else GREY})])
            self.cell_text(vc, [(value, {"size": 9 if not strong else 10.5, "bold": True, "color": color})],
                           align=WD_ALIGN_PARAGRAPH.RIGHT)
            lc.vertical_alignment = vc.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        if right.paragraphs[0].text == "" and len(right.paragraphs) >= 1 and right._tc.index(right.paragraphs[0]._p) == 1:
            right._tc.remove(right.paragraphs[0]._p)
        self.spacer(8)

    # ---- judul bagian kecil
    def heading(self, text):
        p = self.para(before=4, after=3)
        self.run(p, text.upper(), size=8, bold=True, color=self.b.primary, spacing=1)
        para_bottom_border(p, 6, self.b.accent, 2)
        return p

    def terms(self, items):
        t = self.doc.add_table(rows=len(items), cols=3)
        table_no_borders(t)
        table_fixed(t, [7, 34, self.content_w - 41])
        for i, (label, text) in enumerate(items):
            n, l, v = t.rows[i].cells
            keep_row_together(t.rows[i])
            for c in (n, l, v):
                set_cell_margins(c, 25, 25, 0 if c is n else 40, 60)
            self.cell_text(n, [(f"{i + 1}.", {"size": 8, "color": GREY})])
            self.cell_text(l, [(label, {"size": 8, "bold": True})])
            lines = text if isinstance(text, list) else [text]
            self.cell_text(v, [(x, {"size": 8, "color": "3A4553"}) for x in lines])
        self.spacer(6)

    def info_box(self, title, lines, tint=True):
        t = self.doc.add_table(rows=1, cols=1)
        table_no_borders(t)
        table_fixed(t, [self.content_w])
        c = t.rows[0].cells[0]
        set_cell_margins(c, 100, 100, 140, 140)
        if tint:
            set_cell_shading(c, self.b.tint)
        set_cell_borders(c, left=(24, self.b.accent))
        content = [(title.upper(), {"size": 7.5, "bold": True, "color": self.b.primary, "spacing": 1})]
        content += [(x, {"size": 8.5}) if not isinstance(x, tuple) else x for x in lines]
        self.cell_text(c, content)
        self.spacer(8)

    def signatures(self, blocks, height_mm=20):
        n = len(blocks)
        gap = 6
        w = (self.content_w - gap * (n - 1)) / n
        t = self.doc.add_table(rows=3, cols=n * 2 - 1)
        table_no_borders(t)
        widths = []
        for i in range(n):
            widths.append(w)
            if i < n - 1:
                widths.append(gap)
        table_fixed(t, widths)
        for r in t.rows:
            keep_row_together(r)
        tr_pr = t.rows[1]._tr.get_or_add_trPr()
        h = OxmlElement("w:trHeight")
        h.set(qn("w:val"), str(int(Mm(height_mm).twips)))
        tr_pr.append(h)
        for i, block in enumerate(blocks):
            if block is None:
                continue
            label, name, role = block
            top, mid, bottom = (t.rows[k].cells[i * 2] for k in range(3))
            self.cell_text(top, [(label.upper(), {"size": 7.5, "bold": True, "color": self.b.primary, "spacing": 1})])
            set_cell_borders(bottom, top=(8, INK))
            set_cell_margins(bottom, 50, 0, 0, 0)
            self.cell_text(bottom, [(name or "Name :", {"size": 9, "bold": bool(name)}),
                                    (role or "Date :", {"size": 8, "color": GREY})])
            set_cell_margins(top, 0, 0, 0, 0)
            set_cell_margins(mid, 0, 0, 0, 0)

    def signature_right(self, name, title="Authorized Signature", height_mm=24, width_mm=42):
        """Satu tanda tangan perusahaan di pojok kanan bawah: ruang ttd/stempel, nama (tebal, bergaris bawah), jabatan."""
        t = self.doc.add_table(rows=2, cols=2)
        table_no_borders(t, zero_cell_margins=True)
        table_fixed(t, [self.content_w - width_mm, width_mm])
        for r in t.rows:
            keep_row_together(r)
        tr_pr = t.rows[0]._tr.get_or_add_trPr()
        h = OxmlElement("w:trHeight")
        h.set(qn("w:val"), str(int(Mm(height_mm).twips)))
        tr_pr.append(h)
        cell = t.rows[1].cells[1]
        cell.paragraphs[0].text = ""
        p1 = cell.paragraphs[0]
        para_spacing(p1, 0, 0, 1.1)
        p1.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r1 = self.run(p1, name or " ", size=10, bold=True, color=INK)
        r1.font.underline = True
        p2 = cell.add_paragraph()
        para_spacing(p2, 0, 0, 1.1)
        p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
        self.run(p2, title, size=9.5, bold=True, color=INK)

    def save(self) -> bytes:
        buf = io.BytesIO()
        self.doc.save(buf)
        return buf.getvalue()


# --------------------------------------------------------------------------- dokumen
def _qty_text(item):
    qty = f"{fmt_qty(item['qty'])} {item.get('unit', 'unit')}"
    if item.get("days"):
        qty += f" × {item['days']} day{'s' if int(item['days']) != 1 else ''}"
    return qty

def _qty_only(item):
    return f"{fmt_qty(item['qty'])} {item.get('unit', 'unit')}"


def _duration_text(item):
    days = item.get("days")
    if not days:
        return "–"
    days = int(days)
    return f"{days} day{'s' if days != 1 else ''}"


def _price_items_table(d, data, desc_label):
    """Tabel harga: kolom Qty (jumlah alat) dan Duration (hari) dipisah.
    Kolom Duration hanya muncul bila ada item yang memakai hari (rental/survey)."""
    items = [i for i in data["items"] if not i.get("group")]
    has_duration = any(i.get("days") for i in items)
    rows = []
    no = 0
    for item in data["items"]:
        if item.get("group"):
            rows.append({"group": item["group"]})
            continue
        no += 1
        amount = Decimal(str(item["qty"])) * Decimal(str(item["price"])) * Decimal(str(item.get("days") or 1))
        cells = [str(no), [item["name"], *item.get("details", [])], _qty_only(item)]
        if has_duration:
            cells.append(_duration_text(item))
        cells += [fmt_idr(item["price"]), fmt_idr(amount)]
        rows.append({"cells": cells})
    if has_duration:
        price_label = "Rate / day (IDR)" if data.get("kind", "RENTAL") == "RENTAL" else "Rate (IDR)"
        columns = ["No", desc_label, "Qty", "Duration", price_label, "Amount (IDR)"]
        widths = [10, d.content_w - 10 - 18 - 22 - 30 - 31, 18, 22, 30, 31]
        d.items_table(columns, rows, widths, numeric_cols=(4, 5), center_cols=(0, 2, 3))
    else:
        columns = ["No", desc_label, "Qty", "Unit price (IDR)", "Amount (IDR)"]
        widths = [10, d.content_w - 10 - 22 - 32 - 32, 22, 32, 32]
        d.items_table(columns, rows, widths, numeric_cols=(3, 4), center_cols=(0, 2))


def _sum(data):
    if data.get("total") is not None:
        return (Decimal(str(data.get("subtotal") or 0)), Decimal(str(data.get("tax") or 0)),
                Decimal(str(data["total"])))
    subtotal = sum(Decimal(str(i["qty"])) * Decimal(str(i["price"])) * Decimal(str(i.get("days") or 1))
                   for i in data["items"] if not i.get("group"))
    tax = (subtotal * Decimal(str(data.get("tax_rate", 0)))).quantize(Decimal("1")) if data.get("tax_rate") else Decimal(0)
    return subtotal, tax, subtotal + tax


DEFAULT_TERMS = {
    "QR": [
        ("Payment", "By telegraphic transfer: 50% down payment with Purchase Order; 50% balance after demobilisation."),
        ("Validity", "Prices are firm and valid for fourteen (14) days from the date of this quotation."),
        ("Price", "Prices quoted in Indonesian Rupiah (IDR)."),
        ("Delivery", "Equipment delivered to the CLIENT's designated address in Indonesia within one (1) day of receiving a firm order or Purchase Order."),
    ],
    "SNV": [
        ("Prices", "All prices are in Indonesian Rupiah (IDR) and exclude PPN unless stated."),
        ("Delivery", "Equipment available ex SUPPLIER premises in Jakarta within one (1) day after receipt of Purchase Order. Mobilisation to site and return are the CLIENT's responsibility. Availability is subject to prior commitments."),
        ("Payment", "50% down payment with Purchase Order; 50% balance after demobilisation, by bank transfer."),
        ("Insurance", "CLIENT is responsible for all-risk insurance, including loss or damage, based on the declared equipment value."),
        ("Late payment", "Interest of 1% per month applies to overdue amounts."),
        ("Validity", "This quotation is valid for fourteen (14) days from the date above."),
    ],
}
DEFAULT_TERMS_BY_KIND = {
    "PENJUALAN": [
        ("Prices", "All prices are in Indonesian Rupiah (IDR); PPN as stated above."),
        ("Delivery", "Delivery time as stated in lead time, counted from receipt of Purchase Order and down payment."),
        ("Payment", "As agreed in the Purchase Order, by bank transfer."),
        ("Warranty", "Manufacturer warranty applies from the date of delivery."),
        ("Validity", "This quotation is valid for fourteen (14) days from the date above."),
    ],
    "JASA_SURVEY": [
        ("Prices", "All prices are in Indonesian Rupiah (IDR); PPN as stated above."),
        ("Scope", "Scope of work, deliverables and schedule as described above; changes are subject to a variation order."),
        ("Payment", "As agreed in the Purchase Order / contract, by bank transfer."),
        ("Standby", "Weather and site-access standby beyond the stated duration is charged at the daily rate."),
        ("Validity", "This quotation is valid for fourteen (14) days from the date above."),
    ],
}


def default_terms(company, kind):
    return DEFAULT_TERMS_BY_KIND.get(kind) or DEFAULT_TERMS.get(company, DEFAULT_TERMS["SNV"])


KIND_SUBTITLE = {"RENTAL": "Equipment rental", "PENJUALAN": "Product sale", "JASA_SURVEY": "Survey services"}


def build_quotation(company: str, data: dict) -> bytes:
    b = BRANDS[company]
    d = DocBuilder(b)
    d.header_footer("Quotation", data["number"])
    d.title_block("QUOTATION", KIND_SUBTITLE.get(data.get("kind", "RENTAL"), ""), [
        ("Quote No.", data["number"]), ("Date", data["date"]), ("Valid until", data["valid_until"]),
        ("Lead time", data.get("lead_time", "-")),
    ])
    d.party_boxes([
        ("Bill to", [data["client"]["name"], *data["client"].get("address", []), f"Attn. {data['client'].get('attn', '-')}"]),
        ("Ship to / Site", [data.get("site", {}).get("name", data["client"]["name"]), *data.get("site", {}).get("lines", [])]),
        ("Prepared by", [data["rep"]["name"], data["rep"].get("title", "Business Development"), data["rep"]["email"]]),
    ])
    _price_items_table(d, data, "Item & description")
    subtotal, tax, total = _sum(data)
    tot_rows = [("Subtotal", fmt_idr(subtotal), False)]
    if tax:
        tot_rows.append((data.get("tax_label") or f"PPN {int(Decimal(str(data['tax_rate'])) * 100)}%", fmt_idr(tax), False))
    tot_rows.append(("TOTAL (IDR)", fmt_idr(total), True))
    d.totals([("Amount in words", {"size": 7, "bold": True, "color": b.primary, "spacing": 1, "caps": True}),
              (amount_in_words(total), {"size": 8.5, "italic": True, "color": "3A4553"})], tot_rows)
    d.heading("Terms & conditions")
    d.terms(data["terms"])
    p = d.para(after=4)
    d.run(p, "To confirm the order, please issue a Purchase Order referring to the quotation number above.",
          size=8, color=GREY)
    d.signature_right(data.get("signer") or data["rep"]["name"], data.get("signer_title", "Authorized Signature"))
    return d.save()


def build_invoice(company: str, data: dict) -> bytes:
    b = BRANDS[company]
    d = DocBuilder(b)
    d.header_footer("Invoice", data["number"])
    meta = [
        ("Invoice No.", data["number"]), ("Invoice date", data["date"]), ("Due date", data["due_date"]),
        ("Quotation", data.get("quotation", "-")), ("PO ref.", data.get("po", "-")),
    ]
    if data.get("status"):
        meta.append(("Status", data["status"]))
    d.title_block("INVOICE", KIND_SUBTITLE.get(data.get("kind", "RENTAL"), ""), meta)
    d.party_boxes([
        ("Bill to", [data["client"]["name"], *data["client"].get("address", []), f"Attn. {data['client'].get('attn', '-')}"]),
        ("Project / period", [data.get("project", "-"), *data.get("project_lines", [])]),
    ])
    _price_items_table(d, data, "Description")
    subtotal, tax, total = _sum(data)
    tot_rows = [("Subtotal", fmt_idr(subtotal), False)]
    if tax:
        tot_rows.append((data.get("tax_label") or f"PPN {int(Decimal(str(data['tax_rate'])) * 100)}%", fmt_idr(tax), False))
    if data.get("paid"):
        tot_rows.append(("Less: received", "(" + fmt_idr(data["paid"]) + ")", False))
    due = total - Decimal(str(data.get("paid") or 0))
    tot_rows.append(("AMOUNT DUE (IDR)", fmt_idr(due), True))
    d.totals([("Amount in words", {"size": 7, "bold": True, "color": b.primary, "spacing": 1, "caps": True}),
              (amount_in_words(due), {"size": 8.5, "italic": True, "color": "3A4553"}),
              ("", {"size": 4}),
              ("Payment terms", {"size": 7, "bold": True, "color": b.primary, "spacing": 1, "caps": True}),
              *[(x, {"size": 8.5, "color": "3A4553"}) for x in data.get("payment_terms", [])]], tot_rows)
    d.info_box("Payment instructions", [
        ("Please remit by telegraphic transfer, without deducting bank charges, to:", {"size": 8.5, "color": GREY}),
        *[(line, {"size": 9, "bold": i in (0, 2)}) for i, line in enumerate(b.bank)],
        (f"Please quote invoice no. {data['number']} as the payment reference.", {"size": 8, "color": GREY, "italic": True}),
    ])
    d.signature_right(data.get("signer", "Finance Department"), data.get("signer_title", "Authorized Signature"))
    return d.save()


def build_delivery_order(company: str, data: dict) -> bytes:
    b = BRANDS[company]
    d = DocBuilder(b)
    d.header_footer("Delivery Order", data["number"])
    d.title_block("DELIVERY ORDER", data.get("purpose", "Equipment delivery"), [
        ("DO No.", data["number"]), ("Date", data["date"]), ("Reference", data.get("reference", "-")),
        ("PO ref.", data.get("po", "-")),
    ])
    d.party_boxes([
        ("Deliver to", [data["client"]["name"], *data.get("site_lines", []), f"Attn. {data['client'].get('attn', '-')}"]),
        ("Shipment", [data.get("ship_via", "-"), *data.get("ship_lines", [])]),
        ("Contact", [data["rep"]["name"], data["rep"].get("phone", ""), data["rep"]["email"]]),
    ])
    rows = []
    no = 0
    for item in data["items"]:
        if item.get("group"):
            rows.append({"group": item["group"]})
            continue
        no += 1
        rows.append({"cells": [str(no), [item["name"], *item.get("details", [])], item.get("serial", "-"),
                               f"{fmt_qty(item['qty'])} {item.get('unit', 'unit')}", item.get("condition", "Good"), ""]})
    d.items_table(["No", "Item & accessories", "Serial no.", "Qty", "Condition", "Check ✓"],
                  rows, [10, d.content_w - 10 - 36 - 20 - 22 - 18, 36, 20, 22, 18], center_cols=(0, 3, 4, 5))
    d.spacer(8)
    d.info_box("Receipt statement", [
        ("The goods listed above have been received complete and in good working condition, unless noted otherwise below.",
         {"size": 8.5}),
        ("Remarks : ____________________________________________________________________________________",
         {"size": 8.5, "color": GREY}),
    ])
    d.signatures([
        ("Prepared by (warehouse)", data.get("prepared_by", ""), "Date :"),
        ("Delivered by", data.get("delivered_by", ""), "Date :"),
        ("Received by (client)", "", "Name, date, time & stamp"),
    ], height_mm=22)
    return d.save()





def build_generic(company: str, data: dict) -> bytes:
    """Dokumen umum dengan gaya yang sama: dipakai untuk berita acara pengembalian,
    QC Report, Daily Progress Report, BAST, dan DO penjualan.

    data: title, subtitle, number, footer_title, meta[(label, value)], parties[(label, [lines])],
          sections[...] — tiap section salah satu dari:
            {"heading": str}
            {"table": {"columns": [...], "widths": [...], "rows": [...], "center": (), "numeric": ()}}
            {"box": {"title": str, "lines": [...]}}
            {"text": str}
          signatures[(label, name, role) | None], signature_height
    """
    b = BRANDS[company]
    d = DocBuilder(b)
    d.header_footer(data.get("footer_title", data["title"].title()), data["number"])
    d.title_block(data["title"], data.get("subtitle", ""), data.get("meta", []))
    if data.get("parties"):
        d.party_boxes(data["parties"])
    for section in data.get("sections", []):
        if "heading" in section:
            d.heading(section["heading"])
        elif "table" in section:
            t = section["table"]
            widths = list(t["widths"])
            if None in widths:
                fixed = sum(w for w in widths if w)
                widths = [w if w else d.content_w - fixed for w in widths]
            d.items_table(t["columns"], t["rows"], widths, numeric_cols=t.get("numeric", ()),
                          center_cols=t.get("center", ()))
            d.spacer(8)
        elif "box" in section:
            d.info_box(section["box"]["title"], section["box"]["lines"])
        elif "text" in section:
            p = d.para(after=6)
            d.run(p, section["text"], size=8.5, color="3A4553")
    if data.get("signatures"):
        d.signatures(data["signatures"], height_mm=data.get("signature_height", 20))
    return d.save()


BUILDERS = {"quotation": build_quotation, "invoice": build_invoice, "delivery_order": build_delivery_order,
            "generic": build_generic}
