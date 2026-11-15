"""Lengkapi data resmi perusahaan (alamat, telepon, email, NPWP)."""
from django.db import migrations

SQL = """
DO $$
BEGIN
    IF to_regclass('public.perusahaan') IS NULL THEN
        RETURN;
    END IF;
    UPDATE perusahaan SET
        alamat = 'Indonesia Stock Exchange Tower II, 17th Floor, Jl. Jend. Sudirman Kav. 52-53, Jakarta Selatan, Indonesia 12190',
        telepon = '+62 21 5291 7489', email = 'sales@oseanaprawirapersada.com', nomor_pajak = '86.208.779.8-012.000'
    WHERE kode = 'QR';
    UPDATE perusahaan SET
        alamat = 'Sahid Sudirman Center 56th Floor, Jl. Jenderal Sudirman No. 86, Jakarta 10220, Indonesia',
        telepon = '+62 21 5799 8020', email = 'sales@seanavgroup.co.id', nomor_pajak = '1000 0000 0711 8487'
    WHERE kode = 'SNV';
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0004_serah_terima_checklist")]
    operations = [migrations.RunSQL(SQL, migrations.RunSQL.noop)]
