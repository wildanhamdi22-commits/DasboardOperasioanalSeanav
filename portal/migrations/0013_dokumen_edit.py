from django.db import migrations

SQL = """
CREATE TABLE IF NOT EXISTS dokumen_edit (
    jenis VARCHAR(30) NOT NULL,
    ref_id BIGINT NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'::jsonb,
    diubah_oleh VARCHAR(150),
    diubah_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (jenis, ref_id)
);
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0012_impor_mru_smc")]
    operations = [migrations.RunSQL(SQL, "DROP TABLE IF EXISTS dokumen_edit;")]
