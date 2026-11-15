"""Checklist pengecekan pada serah terima rental (pengembalian alat)."""
from django.db import migrations

SQL = """
DO $$
BEGIN
    IF to_regclass('public.serah_terima_rental') IS NOT NULL THEN
        ALTER TABLE serah_terima_rental ADD COLUMN IF NOT EXISTS checklist JSONB;
    END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0003_modul_rental_penjualan_survey")]
    operations = [migrations.RunSQL(SQL, migrations.RunSQL.noop)]
