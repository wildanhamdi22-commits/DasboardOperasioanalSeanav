"""Pinjam bendera umum: kategori pekerjaan bebas + jenis klien (umum / instansi pemerintah pemungut pajak).

Klien pemerintah: PPN dipungut bendahara, PPh 22 (barang) dan PPh 23 (jasa) dipotong, sehingga
yang masuk ke rekening = neto. Fee perusahaan dihitung dari neto. Data lama menjadi
kategori "Penelitian" + klien "Umum" (neto = total, tidak ada yang berubah).
"""
from django.db import migrations

SQL = """
ALTER TABLE pinjam_bendera
    ADD COLUMN IF NOT EXISTS kategori VARCHAR(60) NOT NULL DEFAULT 'Penelitian',
    ADD COLUMN IF NOT EXISTS jenis_klien VARCHAR(12) NOT NULL DEFAULT 'UMUM',
    ADD COLUMN IF NOT EXISTS ppn_dipungut BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS tarif_pph22 NUMERIC(6,2) NOT NULL DEFAULT 1.5,
    ADD COLUMN IF NOT EXISTS tarif_pph23 NUMERIC(6,2) NOT NULL DEFAULT 2,
    ADD COLUMN IF NOT EXISTS pph22 NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS pph23 NUMERIC(18,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS neto NUMERIC(18,2),
    ADD COLUMN IF NOT EXISTS data_pemerintah JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS dana_masuk_pada DATE,
    ADD COLUMN IF NOT EXISTS diteruskan_pada DATE,
    ADD COLUMN IF NOT EXISTS bupot_diterima BOOLEAN NOT NULL DEFAULT FALSE;
UPDATE pinjam_bendera SET neto = total WHERE neto IS NULL;
ALTER TABLE pinjam_bendera DROP CONSTRAINT IF EXISTS pinjam_bendera_status_check;
ALTER TABLE pinjam_bendera ADD CONSTRAINT pinjam_bendera_status_check
    CHECK (status IN ('DRAFT', 'DOKUMEN_TERBIT', 'DANA_MASUK', 'DITERUSKAN', 'FEE_DITERIMA', 'DIBATALKAN'));
ALTER TABLE pinjam_bendera_item
    ADD COLUMN IF NOT EXISTS jenis VARCHAR(6) NOT NULL DEFAULT 'JASA';
CREATE INDEX IF NOT EXISTS idx_pinjam_bendera_kategori ON pinjam_bendera (kategori);
"""

REVERSE = """
ALTER TABLE pinjam_bendera_item DROP COLUMN IF EXISTS jenis;
UPDATE pinjam_bendera SET status = 'DOKUMEN_TERBIT' WHERE status IN ('DANA_MASUK', 'DITERUSKAN');
ALTER TABLE pinjam_bendera DROP CONSTRAINT IF EXISTS pinjam_bendera_status_check;
ALTER TABLE pinjam_bendera ADD CONSTRAINT pinjam_bendera_status_check
    CHECK (status IN ('DRAFT', 'DOKUMEN_TERBIT', 'FEE_DITERIMA', 'DIBATALKAN'));
ALTER TABLE pinjam_bendera
    DROP COLUMN IF EXISTS kategori, DROP COLUMN IF EXISTS jenis_klien, DROP COLUMN IF EXISTS ppn_dipungut,
    DROP COLUMN IF EXISTS tarif_pph22, DROP COLUMN IF EXISTS tarif_pph23, DROP COLUMN IF EXISTS pph22,
    DROP COLUMN IF EXISTS pph23, DROP COLUMN IF EXISTS neto, DROP COLUMN IF EXISTS data_pemerintah,
    DROP COLUMN IF EXISTS dana_masuk_pada, DROP COLUMN IF EXISTS diteruskan_pada, DROP COLUMN IF EXISTS bupot_diterima;
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0014_pinjam_bendera")]
    operations = [migrations.RunSQL(SQL, REVERSE)]
