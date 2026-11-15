from django.db import migrations

# Absensi dipusatkan di Workshop Office (Jl. KH Abdullah Syafei No. 52, Manggarai Selatan, Tebet).
# Koordinat dari Google Maps "WORKSHOP OFFICE": -6.2239423, 106.8503391 (UTM 48S E 704.726 / N 9.311.682).
SQL = """
INSERT INTO titik_absensi (nama, latitude, longitude, radius_m)
SELECT 'Workshop Office - Jl. KH Abdullah Syafei 52', -6.2239423, 106.8503391, 250
WHERE NOT EXISTS (SELECT 1 FROM titik_absensi WHERE nama LIKE 'Workshop Office%');
UPDATE titik_absensi SET aktif = false WHERE nama = 'Kantor Oseana - IDX Tower II';
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0006_titik_absensi")]
    operations = [migrations.RunSQL(SQL, migrations.RunSQL.noop)]
