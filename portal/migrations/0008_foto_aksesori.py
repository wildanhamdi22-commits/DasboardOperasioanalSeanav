from django.db import migrations

SQL = """
DO $$
BEGIN
  IF to_regclass('public.paket_unit_item') IS NOT NULL THEN
    ALTER TABLE paket_unit_item ADD COLUMN IF NOT EXISTS foto_url TEXT;
  END IF;
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0007_titik_workshop")]
    operations = [migrations.RunSQL(SQL, migrations.RunSQL.noop)]
