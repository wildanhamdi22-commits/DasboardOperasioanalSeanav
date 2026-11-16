"""Impor tambahan: MRU SMC S/N 1083116 (dari DO_QR26168). Data di portal/data_import/alat_impor_mru.json."""
import importlib
from pathlib import Path

from django.db import migrations

base = importlib.import_module("portal.migrations.0011_impor_dokumen_aksesoris")
DATA = Path(__file__).resolve().parent.parent / "data_import" / "alat_impor_mru.json"


def forwards(apps, schema_editor):
    original = base.DATA
    base.DATA = DATA
    try:
        base.forwards(apps, schema_editor)
    finally:
        base.DATA = original


class Migration(migrations.Migration):
    dependencies = [("portal", "0011_impor_dokumen_aksesoris")]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
