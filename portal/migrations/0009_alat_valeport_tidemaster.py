"""Dikosongkan: input otomatis alat Valeport TideMaster dibatalkan atas permintaan pengguna.

Berkas tetap ada agar urutan migrasi konsisten. Bila sempat diterapkan, migrasi 0010 menghapus datanya.
"""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("portal", "0008_foto_aksesori")]
    operations = []
