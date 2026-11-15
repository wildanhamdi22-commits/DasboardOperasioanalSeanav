from django.db import migrations

SQL = """
CREATE TABLE IF NOT EXISTS titik_absensi (
    id          bigserial PRIMARY KEY,
    nama        varchar(120) NOT NULL,
    latitude    numeric(10, 7) NOT NULL,
    longitude   numeric(10, 7) NOT NULL,
    radius_m    integer NOT NULL DEFAULT 150 CHECK (radius_m BETWEEN 20 AND 5000),
    aktif       boolean NOT NULL DEFAULT true,
    dibuat_pada timestamptz NOT NULL DEFAULT now()
);
INSERT INTO titik_absensi (nama, latitude, longitude, radius_m)
SELECT 'Kantor Oseana - IDX Tower II', -6.2237, 106.8085, 200
WHERE NOT EXISTS (SELECT 1 FROM titik_absensi);
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0005_data_perusahaan")]
    operations = [migrations.RunSQL(SQL, "DROP TABLE IF EXISTS titik_absensi;")]
