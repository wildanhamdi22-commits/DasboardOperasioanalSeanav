"""Konversi dokumen Word (.docx) ke PDF.

- Windows dengan Microsoft Word: memakai Word (hasil paling identik dengan template).
- Linux/hosting atau Windows tanpa Word: memakai LibreOffice (`soffice --headless`).
Atur lewat env PDF_CONVERTER = auto | word | libreoffice.
"""
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings

logger = logging.getLogger(__name__)


class PdfConversionError(Exception):
    pass


def _word(docx_path, pdf_path):
    import pythoncom  # noqa: WPS433 - hanya tersedia di Windows
    import win32com.client

    pythoncom.CoInitialize()
    word = doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(docx_path.resolve()), ReadOnly=True, AddToRecentFiles=False)
        doc.ExportAsFixedFormat(str(pdf_path.resolve()), 17)
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:  # pragma: no cover
                logger.exception("Gagal menutup dokumen Word sementara")
        if word is not None:
            try:
                word.Quit()
            except Exception:  # pragma: no cover
                logger.exception("Gagal menutup Microsoft Word")
        pythoncom.CoUninitialize()


def _find_soffice():
    configured = getattr(settings, "LIBREOFFICE_BIN", "soffice")
    found = shutil.which(configured) or shutil.which("soffice") or shutil.which("libreoffice")
    if found:
        return found
    for candidate in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                      r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
                      "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice",
                      "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        if os.path.exists(candidate):
            return candidate
    return None


def _libreoffice(docx_path, out_dir):
    soffice = _find_soffice()
    if not soffice:
        raise PdfConversionError("LibreOffice tidak ditemukan di server.")
    profile = Path(out_dir) / "lo_profile"
    cmd = [soffice, "--headless", "--norestore", "--nolockcheck",
           f"-env:UserInstallation={profile.resolve().as_uri()}",
           "--convert-to", "pdf", "--outdir", str(out_dir), str(docx_path)]
    result = subprocess.run(cmd, capture_output=True, timeout=180, check=False)
    if result.returncode != 0:
        raise PdfConversionError(
            "LibreOffice gagal mengonversi dokumen: " + result.stderr.decode(errors="ignore")[:300])


def docx_to_pdf(docx_bytes, stem="dokumen"):
    """Kembalikan bytes PDF dari bytes DOCX. Melempar PdfConversionError bila gagal."""
    mode = getattr(settings, "PDF_CONVERTER", "auto").lower()
    safe_stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in stem) or "dokumen"
    with TemporaryDirectory(prefix="seanav_pdf_", ignore_cleanup_errors=True) as tmp:
        docx_path = Path(tmp) / f"{safe_stem}.docx"
        pdf_path = Path(tmp) / f"{safe_stem}.pdf"
        docx_path.write_bytes(docx_bytes)
        errors = []
        order = []
        if mode in ("auto", "word") and sys.platform == "win32":
            order.append("word")
        if mode in ("auto", "libreoffice"):
            order.append("libreoffice")
        if not order:
            raise PdfConversionError("Microsoft Word hanya tersedia di Windows. Gunakan PDF_CONVERTER=libreoffice.")
        for engine in order:
            try:
                if engine == "word":
                    _word(docx_path, pdf_path)
                else:
                    _libreoffice(docx_path, tmp)
                if pdf_path.is_file() and pdf_path.stat().st_size > 0:
                    return pdf_path.read_bytes()
                errors.append(f"{engine}: file PDF tidak terbentuk")
            except ImportError:
                errors.append("word: pywin32/Microsoft Word tidak tersedia")
            except PdfConversionError as exc:
                errors.append(str(exc))
            except Exception as exc:  # pragma: no cover - tergantung Word/LibreOffice
                logger.exception("Konversi PDF via %s gagal", engine)
                errors.append(f"{engine}: {exc}")
        raise PdfConversionError("; ".join(errors))
