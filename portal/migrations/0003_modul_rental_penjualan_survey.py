"""Struktur 3 modul bisnis: Rental, Penjualan, Jasa Survey.

- 3 kategori alat: Geodetic, Geophysic, Metocean
- unit_alat.modul (RENTAL / PENJUALAN)
- katalog produk jual, pesanan indent, unit datang (SN), QC
- lampiran dokumen (DO bertanda tangan, foto, BAST)
- invoice termin untuk jasa survey, laporan harian & deliverable proyek

Semua perintah idempoten dan dilewati bila skema bisnis belum dibuat.
"""
from django.db import migrations

SQL = r"""
DO $$
BEGIN
    IF to_regclass('public.kategori_alat') IS NULL THEN
        RETURN;
    END IF;

    INSERT INTO kategori_alat (nama, deskripsi) VALUES
        ('Geodetic', 'Peralatan geodesi & positioning'),
        ('Geophysic', 'Peralatan geofisika'),
        ('Metocean', 'Peralatan meteorologi & oseanografi')
    ON CONFLICT (nama) DO NOTHING;

    ALTER TABLE unit_alat ADD COLUMN IF NOT EXISTS modul VARCHAR(20) NOT NULL DEFAULT 'RENTAL';

    CREATE TABLE IF NOT EXISTS produk_jual (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        kategori_alat_id BIGINT NOT NULL REFERENCES kategori_alat(id),
        merek VARCHAR(120),
        nama VARCHAR(200) NOT NULL,
        deskripsi TEXT,
        harga_jual NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (harga_jual >= 0),
        satuan VARCHAR(40) NOT NULL DEFAULT 'unit',
        garansi VARCHAR(120),
        waktu_indent VARCHAR(80),
        aktif BOOLEAN NOT NULL DEFAULT TRUE,
        dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
        dibuat_oleh BIGINT REFERENCES pengguna(id)
    );

    ALTER TABLE quotation_item ADD COLUMN IF NOT EXISTS produk_jual_id BIGINT REFERENCES produk_jual(id);
    ALTER TABLE pesanan_penjualan_item ADD COLUMN IF NOT EXISTS produk_jual_id BIGINT REFERENCES produk_jual(id);

    ALTER TABLE pesanan_penjualan
        ADD COLUMN IF NOT EXISTS pemasok VARCHAR(200),
        ADD COLUMN IF NOT EXISTS no_po_pemasok VARCHAR(100),
        ADD COLUMN IF NOT EXISTS tanggal_pesan_pemasok DATE,
        ADD COLUMN IF NOT EXISTS eta DATE,
        ADD COLUMN IF NOT EXISTS alamat_kirim TEXT,
        ADD COLUMN IF NOT EXISTS latitude NUMERIC(10,7),
        ADD COLUMN IF NOT EXISTS longitude NUMERIC(10,7),
        ADD COLUMN IF NOT EXISTS tanggal_kirim TIMESTAMPTZ,
        ADD COLUMN IF NOT EXISTS penerima VARCHAR(180),
        ADD COLUMN IF NOT EXISTS pengirim VARCHAR(180);

    CREATE TABLE IF NOT EXISTS unit_penjualan (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        pesanan_item_id BIGINT NOT NULL REFERENCES pesanan_penjualan_item(id) ON DELETE CASCADE,
        nomor_seri VARCHAR(120) NOT NULL,
        sn_packing_list VARCHAR(120),
        tanggal_datang DATE NOT NULL,
        status_qc VARCHAR(20) NOT NULL DEFAULT 'BELUM'
            CHECK (status_qc IN ('BELUM', 'LULUS', 'CATATAN', 'GAGAL')),
        dicatat_oleh BIGINT REFERENCES pengguna(id),
        dicatat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (pesanan_item_id, nomor_seri)
    );

    CREATE TABLE IF NOT EXISTS qc_penjualan (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        unit_penjualan_id BIGINT NOT NULL REFERENCES unit_penjualan(id) ON DELETE CASCADE,
        checklist JSONB NOT NULL DEFAULT '{}'::jsonb,
        firmware VARCHAR(80),
        hasil VARCHAR(20) NOT NULL CHECK (hasil IN ('LULUS', 'CATATAN', 'GAGAL')),
        catatan TEXT,
        pemeriksa_id BIGINT REFERENCES pengguna(id),
        diperiksa_pada TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE IF NOT EXISTS lampiran_dokumen (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        entitas VARCHAR(40) NOT NULL,
        entitas_id BIGINT NOT NULL,
        jenis VARCHAR(40) NOT NULL,
        judul VARCHAR(200),
        file_path TEXT NOT NULL,
        nama_file VARCHAR(255),
        catatan TEXT,
        diunggah_oleh BIGINT REFERENCES pengguna(id),
        diunggah_pada TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX IF NOT EXISTS lampiran_dokumen_entitas_idx ON lampiran_dokumen (entitas, entitas_id);

    ALTER TABLE invoice DROP CONSTRAINT IF EXISTS invoice_quotation_id_key;
    ALTER TABLE invoice
        ADD COLUMN IF NOT EXISTS termin_ke SMALLINT,
        ADD COLUMN IF NOT EXISTS persen_termin NUMERIC(6,2),
        ADD COLUMN IF NOT EXISTS keterangan_termin VARCHAR(160);
    CREATE UNIQUE INDEX IF NOT EXISTS invoice_quotation_termin_uniq
        ON invoice (quotation_id, COALESCE(termin_ke, 0));

    ALTER TABLE proyek
        ADD COLUMN IF NOT EXISTS kru TEXT,
        ADD COLUMN IF NOT EXISTS kapal VARCHAR(200),
        ADD COLUMN IF NOT EXISTS progres NUMERIC(5,2) NOT NULL DEFAULT 0,
        ADD COLUMN IF NOT EXISTS bast_tanggal DATE,
        ADD COLUMN IF NOT EXISTS bast_penerima VARCHAR(180);

    CREATE TABLE IF NOT EXISTS laporan_harian (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        proyek_id BIGINT NOT NULL REFERENCES proyek(id),
        tanggal DATE NOT NULL,
        aktivitas TEXT NOT NULL,
        cuaca VARCHAR(120),
        jam_kerja NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (jam_kerja >= 0),
        jam_standby NUMERIC(5,2) NOT NULL DEFAULT 0 CHECK (jam_standby >= 0),
        progres NUMERIC(5,2) CHECK (progres IS NULL OR progres BETWEEN 0 AND 100),
        kendala TEXT,
        dibuat_oleh BIGINT REFERENCES pengguna(id),
        dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (proyek_id, tanggal)
    );

    CREATE TABLE IF NOT EXISTS deliverable_proyek (
        id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        proyek_id BIGINT NOT NULL REFERENCES proyek(id),
        judul VARCHAR(200) NOT NULL,
        versi VARCHAR(20) NOT NULL DEFAULT 'DRAFT' CHECK (versi IN ('DRAFT', 'FINAL')),
        file_path TEXT,
        nama_file VARCHAR(255),
        catatan TEXT,
        diunggah_oleh BIGINT REFERENCES pengguna(id),
        diunggah_pada TIMESTAMPTZ NOT NULL DEFAULT now()
    );
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0002_izin_peran_absensi")]
    operations = [migrations.RunSQL(SQL, migrations.RunSQL.noop)]
