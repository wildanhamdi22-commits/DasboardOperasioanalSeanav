"""Beri role Direktur Utama & Manajer hak menyetujui izin dan melihat rekap absensi."""
from django.db import migrations

SQL = """
DO $$
BEGIN
    IF to_regclass('public.izin_peran') IS NOT NULL AND to_regclass('public.peran') IS NOT NULL THEN
        INSERT INTO izin_peran (peran_id, sumber, aksi)
        SELECT p.id, 'ABSENSI', x.aksi
        FROM peran p
        CROSS JOIN (VALUES ('LIHAT'), ('SETUJUI')) AS x(aksi)
        WHERE p.kode IN ('DIREKTUR_UTAMA', 'MANAJER')
          AND NOT EXISTS (
              SELECT 1 FROM izin_peran ip
              WHERE ip.peran_id = p.id AND ip.sumber = 'ABSENSI' AND ip.aksi = x.aksi
          );
    END IF;
END $$;
"""

REVERSE = """
DO $$
BEGIN
    IF to_regclass('public.izin_peran') IS NOT NULL THEN
        DELETE FROM izin_peran WHERE sumber = 'ABSENSI';
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0001_initial")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
