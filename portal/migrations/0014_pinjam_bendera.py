"""Pinjam bendera (LPJ): dokumen atas nama perusahaan untuk pertanggungjawaban klien/peneliti.

Terpisah dari quotation, invoice, rental, dan inventaris: alat tidak tercatat disewa dan
transaksi tidak masuk omzet modul lain. Yang tercatat untuk perusahaan hanya fee.
"""
from django.db import migrations

SQL = """
CREATE TABLE IF NOT EXISTS pinjam_bendera (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    perusahaan_id BIGINT NOT NULL REFERENCES perusahaan(id),
    nomor VARCHAR(80) NOT NULL UNIQUE,
    tanggal_dokumen DATE NOT NULL DEFAULT CURRENT_DATE,
    tanggal_kegiatan VARCHAR(120),
    nama_klien VARCHAR(200) NOT NULL,
    instansi VARCHAR(200),
    alamat TEXT,
    judul_penelitian TEXT,
    harga_termasuk_ppn BOOLEAN NOT NULL DEFAULT TRUE,
    tarif_ppn NUMERIC(6,2) NOT NULL DEFAULT 11 CHECK (tarif_ppn >= 0),
    persen_fee NUMERIC(6,2) NOT NULL DEFAULT 3 CHECK (persen_fee >= 0),
    total NUMERIC(18,2) NOT NULL DEFAULT 0,
    dpp NUMERIC(18,2) NOT NULL DEFAULT 0,
    ppn NUMERIC(18,2) NOT NULL DEFAULT 0,
    fee NUMERIC(18,2) NOT NULL DEFAULT 0,
    penanda_tangan VARCHAR(160),
    jabatan_penanda_tangan VARCHAR(120),
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'DOKUMEN_TERBIT', 'FEE_DITERIMA', 'DIBATALKAN')),
    fee_diterima_pada DATE,
    catatan TEXT,
    dibuat_oleh VARCHAR(150),
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    diubah_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS pinjam_bendera_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pinjam_bendera_id BIGINT NOT NULL REFERENCES pinjam_bendera(id) ON DELETE CASCADE,
    urutan INTEGER NOT NULL DEFAULT 1,
    rincian VARCHAR(300) NOT NULL,
    banyaknya NUMERIC(12,2) NOT NULL DEFAULT 1 CHECK (banyaknya > 0),
    satuan VARCHAR(30) NOT NULL DEFAULT 'unit',
    volume NUMERIC(12,2) NOT NULL DEFAULT 1 CHECK (volume > 0),
    satuan_volume VARCHAR(30) NOT NULL DEFAULT 'ls',
    harga_satuan NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (harga_satuan >= 0),
    jumlah NUMERIC(18,2) NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pinjam_bendera_tanggal ON pinjam_bendera (tanggal_dokumen DESC);
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0013_dokumen_edit")]
    operations = [migrations.RunSQL(SQL, "DROP TABLE IF EXISTS pinjam_bendera_item; DROP TABLE IF EXISTS pinjam_bendera;")]
