-- Skema awal PostgreSQL untuk sistem operasional PT Oseana dan PT Seanav.
-- Draft untuk ditinjau; jalankan pada database baru setelah alur final disepakati.
-- Membutuhkan izin membuat extension PostgreSQL btree_gist untuk kalender jadwal.
BEGIN;

CREATE TABLE perusahaan (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kode VARCHAR(10) NOT NULL UNIQUE,
    nama_resmi VARCHAR(200) NOT NULL,
    alamat TEXT,
    telepon VARCHAR(50),
    email VARCHAR(254),
    nomor_pajak VARCHAR(50),
    aktif BOOLEAN NOT NULL DEFAULT TRUE,
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO perusahaan (kode, nama_resmi) VALUES
    ('QR', 'PT Oseana Prawira Persada'),
    ('SNV', 'PT Seanav Maritim Indonesia');

CREATE TABLE pengguna (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nama VARCHAR(160) NOT NULL,
    email VARCHAR(254) NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    aktif BOOLEAN NOT NULL DEFAULT TRUE,
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    terakhir_masuk_pada TIMESTAMPTZ
);

CREATE TABLE peran (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kode VARCHAR(40) NOT NULL UNIQUE,
    nama VARCHAR(100) NOT NULL
);

INSERT INTO peran (kode, nama) VALUES
    ('DIREKTUR_UTAMA', 'Direktur Utama'),
    ('MANAJER', 'Manajer'),
    ('BUSINESS_DEVELOPMENT', 'Business Development'),
    ('BUSINESS_SUPPORT', 'Business Support'),
    ('TECHNICAL_SALES', 'Technical Sales');

CREATE TABLE pengguna_peran (
    pengguna_id BIGINT NOT NULL REFERENCES pengguna(id),
    peran_id BIGINT NOT NULL REFERENCES peran(id),
    PRIMARY KEY (pengguna_id, peran_id)
);

-- Matriks izin awal; aplikasi wajib memeriksa izin ini pada setiap endpoint.
CREATE TABLE izin_peran (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    peran_id BIGINT NOT NULL REFERENCES peran(id),
    sumber VARCHAR(60) NOT NULL,
    aksi VARCHAR(30) NOT NULL CHECK (aksi IN ('LIHAT','BUAT','UBAH','SETUJUI','KELOLA_AKUN')),
    UNIQUE (peran_id, sumber, aksi)
);

INSERT INTO izin_peran (peran_id, sumber, aksi)
SELECT p.id, x.sumber, x.aksi
FROM peran p
CROSS JOIN (VALUES
    ('SEMUA_TRANSAKSI','LIHAT'),
    ('SEMUA_LAPORAN','LIHAT')
) AS x(sumber, aksi)
WHERE p.kode IN ('DIREKTUR_UTAMA','MANAJER');

INSERT INTO izin_peran (peran_id, sumber, aksi)
SELECT p.id, x.sumber, x.aksi
FROM peran p
CROSS JOIN (VALUES
    ('QUOTATION','SETUJUI')
) AS x(sumber, aksi)
WHERE p.kode IN ('DIREKTUR_UTAMA','MANAJER');

INSERT INTO izin_peran (peran_id, sumber, aksi)
SELECT p.id, x.sumber, x.aksi
FROM peran p
CROSS JOIN (VALUES
    ('SEMUA_DATA','LIHAT'),
    ('KLIEN','BUAT'), ('KLIEN','UBAH'),
    ('QUOTATION','BUAT'), ('QUOTATION','UBAH'),
    ('PROYEK','BUAT'), ('PROYEK','UBAH')
) AS x(sumber, aksi)
WHERE p.kode = 'BUSINESS_DEVELOPMENT';

INSERT INTO izin_peran (peran_id, sumber, aksi)
SELECT p.id, x.sumber, x.aksi
FROM peran p
CROSS JOIN (VALUES
    ('SEMUA_DATA','LIHAT'),
    ('TRANSAKSI','UBAH'),
    ('INVENTARIS','BUAT'), ('INVENTARIS','UBAH'),
    ('PAKET_ALAT','BUAT'), ('PAKET_ALAT','UBAH'),
    ('RENTAL','BUAT'), ('RENTAL','UBAH'),
    ('SERAH_TERIMA','BUAT'), ('SERAH_TERIMA','UBAH'),
    ('QUOTATION','BUAT'), ('QUOTATION','UBAH')
) AS x(sumber, aksi)
WHERE p.kode = 'BUSINESS_SUPPORT';

INSERT INTO izin_peran (peran_id, sumber, aksi)
SELECT p.id, x.sumber, x.aksi
FROM peran p
CROSS JOIN (VALUES
    ('SEMUA_DATA','LIHAT'),
    ('MODEL_ALAT','BUAT'), ('MODEL_ALAT','UBAH'),
    ('QUOTATION','BUAT'), ('QUOTATION','UBAH'),
    ('PENUGASAN_ALAT','BUAT'), ('PENUGASAN_ALAT','UBAH'),
    ('SERAH_TERIMA','BUAT')
) AS x(sumber, aksi)
WHERE p.kode = 'TECHNICAL_SALES';

-- Hak baca untuk modul utama dan invoice yang diterbitkan oleh Business Support.
INSERT INTO izin_peran (peran_id, sumber, aksi)
SELECT p.id, x.sumber, x.aksi
FROM peran p
CROSS JOIN (VALUES
    ('SEMUA_DATA','LIHAT'),
    ('INVENTARIS','LIHAT'), ('QUOTATION','LIHAT'), ('INVOICE','LIHAT'),
    ('RENTAL','LIHAT'), ('PROYEK','LIHAT')
) AS x(sumber, aksi)
ON CONFLICT (peran_id, sumber, aksi) DO NOTHING;

INSERT INTO izin_peran (peran_id, sumber, aksi)
SELECT p.id, x.sumber, x.aksi
FROM peran p
CROSS JOIN (VALUES ('INVOICE','BUAT'), ('INVOICE','UBAH')) AS x(sumber, aksi)
WHERE p.kode = 'BUSINESS_SUPPORT'
ON CONFLICT (peran_id, sumber, aksi) DO NOTHING;

INSERT INTO izin_peran (peran_id, sumber, aksi)
SELECT p.id, 'SERAH_TERIMA', 'UBAH'
FROM peran p
WHERE p.kode = 'TECHNICAL_SALES'
ON CONFLICT (peran_id, sumber, aksi) DO NOTHING;

CREATE TABLE klien (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nama_perusahaan VARCHAR(200) NOT NULL,
    alamat TEXT,
    telepon VARCHAR(50),
    email VARCHAR(254),
    aktif BOOLEAN NOT NULL DEFAULT TRUE,
    catatan TEXT,
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    dibuat_oleh BIGINT REFERENCES pengguna(id)
);

CREATE TABLE kontak_klien (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    klien_id BIGINT NOT NULL REFERENCES klien(id),
    nama VARCHAR(160) NOT NULL,
    jabatan VARCHAR(120),
    telepon VARCHAR(50),
    email VARCHAR(254),
    utama BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE lokasi (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nama VARCHAR(160) NOT NULL,
    jenis VARCHAR(40) NOT NULL,
    alamat TEXT,
    penanggung_jawab VARCHAR(160),
    telepon VARCHAR(50),
    aktif BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE kategori_alat (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nama VARCHAR(120) NOT NULL UNIQUE,
    deskripsi TEXT
);

CREATE TABLE model_alat (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kategori_alat_id BIGINT NOT NULL REFERENCES kategori_alat(id),
    merek VARCHAR(120),
    nama_model VARCHAR(160) NOT NULL,
    deskripsi TEXT,
    spesifikasi JSONB NOT NULL DEFAULT '{}'::jsonb,
    manual_url TEXT,
    aktif BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (kategori_alat_id, merek, nama_model)
);

CREATE TABLE unit_alat (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_alat_id BIGINT NOT NULL REFERENCES model_alat(id),
    nomor_seri VARCHAR(120),
    om_sn VARCHAR(120),
    kode_inventaris VARCHAR(100) UNIQUE,
    foto_url TEXT,
    tanggal_beli DATE,
    biaya_perolehan NUMERIC(18,2) CHECK (biaya_perolehan IS NULL OR biaya_perolehan >= 0),
    kondisi VARCHAR(40) NOT NULL DEFAULT 'BAIK',
    status VARCHAR(40) NOT NULL DEFAULT 'TERSEDIA',
    lokasi_id BIGINT REFERENCES lokasi(id),
    dapat_dijual BOOLEAN NOT NULL DEFAULT FALSE,
    catatan TEXT,
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (model_alat_id, nomor_seri)
);

CREATE TABLE paket_alat (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    nama VARCHAR(180) NOT NULL,
    keterangan TEXT,
    aktif BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE paket_alat_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paket_alat_id BIGINT NOT NULL REFERENCES paket_alat(id),
    model_alat_id BIGINT REFERENCES model_alat(id),
    nama_item VARCHAR(180) NOT NULL,
    jumlah_standar NUMERIC(12,2) NOT NULL DEFAULT 1 CHECK (jumlah_standar > 0),
    satuan VARCHAR(40) NOT NULL DEFAULT 'unit',
    wajib_ada BOOLEAN NOT NULL DEFAULT TRUE,
    punya_serial BOOLEAN NOT NULL DEFAULT FALSE,
    catatan TEXT
);

CREATE TABLE paket_unit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paket_alat_id BIGINT NOT NULL REFERENCES paket_alat(id),
    unit_utama_id BIGINT REFERENCES unit_alat(id),
    status VARCHAR(40) NOT NULL DEFAULT 'TERSEDIA',
    lokasi_id BIGINT REFERENCES lokasi(id),
    catatan TEXT,
    UNIQUE (unit_utama_id)
);

CREATE TABLE paket_unit_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paket_unit_id BIGINT NOT NULL REFERENCES paket_unit(id),
    paket_alat_item_id BIGINT NOT NULL REFERENCES paket_alat_item(id),
    unit_alat_id BIGINT REFERENCES unit_alat(id),
    nomor_seri VARCHAR(120),
    jumlah NUMERIC(12,2) NOT NULL CHECK (jumlah > 0),
    kondisi VARCHAR(40) NOT NULL DEFAULT 'BAIK',
    catatan TEXT
);

CREATE TABLE quotation (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    perusahaan_id BIGINT NOT NULL REFERENCES perusahaan(id),
    nomor VARCHAR(80) NOT NULL,
    jenis VARCHAR(30) NOT NULL CHECK (jenis IN ('PENJUALAN','RENTAL','JASA_SURVEY')),
    klien_id BIGINT NOT NULL REFERENCES klien(id),
    tanggal DATE NOT NULL,
    berlaku_sampai DATE,
    versi INTEGER NOT NULL DEFAULT 1 CHECK (versi > 0),
    pic_pengguna_id BIGINT NOT NULL REFERENCES pengguna(id),
    status VARCHAR(30) NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','DIAJUKAN','DISETUJUI','DITOLAK','TERKIRIM','DITERIMA_KLIEN','DITOLAK_KLIEN','KEDALUWARSA','DIBATALKAN')),
    subtotal NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    jenis_diskon VARCHAR(20) CHECK (jenis_diskon IN ('PERSEN','NOMINAL')),
    nilai_diskon NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (nilai_diskon >= 0),
    total_diskon NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total_diskon >= 0),
    jenis_pajak VARCHAR(60),
    tarif_pajak NUMERIC(8,4) NOT NULL DEFAULT 0 CHECK (tarif_pajak >= 0),
    total_pajak NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total_pajak >= 0),
    total NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total >= 0),
    mata_uang CHAR(3) NOT NULL DEFAULT 'IDR' CHECK (mata_uang = 'IDR'),
    dikirim_pada TIMESTAMPTZ,
    dikirim_oleh BIGINT REFERENCES pengguna(id),
    alasan_status_akhir TEXT,
    catatan TEXT,
    lampiran_url TEXT,
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    dibuat_oleh BIGINT NOT NULL REFERENCES pengguna(id),
    diubah_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    diubah_oleh BIGINT REFERENCES pengguna(id),
    UNIQUE (perusahaan_id, nomor),
    CHECK (berlaku_sampai IS NULL OR berlaku_sampai >= tanggal)
);

CREATE TABLE quotation_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    quotation_id BIGINT NOT NULL REFERENCES quotation(id),
    model_alat_id BIGINT REFERENCES model_alat(id),
    deskripsi_item TEXT NOT NULL,
    kuantitas NUMERIC(12,2) NOT NULL CHECK (kuantitas > 0),
    satuan VARCHAR(40) NOT NULL DEFAULT 'unit',
    harga_satuan NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (harga_satuan >= 0),
    jenis_diskon VARCHAR(20) CHECK (jenis_diskon IN ('PERSEN','NOMINAL')),
    nilai_diskon NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (nilai_diskon >= 0),
    total_diskon NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total_diskon >= 0),
    jenis_pajak VARCHAR(60),
    tarif_pajak NUMERIC(8,4) NOT NULL DEFAULT 0 CHECK (tarif_pajak >= 0),
    total_pajak NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total_pajak >= 0),
    subtotal NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    tanggal_mulai_rencana DATE,
    tanggal_selesai_rencana DATE,
    CHECK (tanggal_selesai_rencana IS NULL OR tanggal_mulai_rencana IS NULL OR tanggal_selesai_rencana >= tanggal_mulai_rencana)
);

CREATE TABLE invoice (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    quotation_id BIGINT NOT NULL UNIQUE REFERENCES quotation(id),
    perusahaan_id BIGINT NOT NULL REFERENCES perusahaan(id),
    nomor VARCHAR(100) NOT NULL,
    tanggal DATE NOT NULL,
    jatuh_tempo DATE,
    po_no VARCHAR(100),
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT', 'DITERBITKAN', 'DIBAYAR', 'DIBATALKAN')),
    subtotal NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (subtotal >= 0),
    jenis_pajak VARCHAR(60),
    tarif_pajak NUMERIC(8,4) NOT NULL DEFAULT 0 CHECK (tarif_pajak >= 0),
    total_pajak NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total_pajak >= 0),
    total NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total >= 0),
    syarat_pembayaran TEXT,
    catatan TEXT,
    dibuat_oleh BIGINT NOT NULL REFERENCES pengguna(id),
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    diterbitkan_pada TIMESTAMPTZ,
    dibayar_pada TIMESTAMPTZ,
    UNIQUE (perusahaan_id, nomor),
    CHECK (jatuh_tempo IS NULL OR jatuh_tempo >= tanggal)
);

CREATE TABLE quotation_persetujuan (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    quotation_id BIGINT NOT NULL REFERENCES quotation(id),
    versi_quotation INTEGER NOT NULL CHECK (versi_quotation > 0),
    peran_id BIGINT NOT NULL REFERENCES peran(id),
    pengguna_id BIGINT REFERENCES pengguna(id),
    keputusan VARCHAR(20) NOT NULL DEFAULT 'MENUNGGU' CHECK (keputusan IN ('MENUNGGU','DISETUJUI','DITOLAK')),
    diputuskan_pada TIMESTAMPTZ,
    catatan TEXT,
    UNIQUE (quotation_id, versi_quotation, peran_id)
);

CREATE TABLE pesanan_penjualan (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    perusahaan_id BIGINT NOT NULL REFERENCES perusahaan(id),
    nomor_pesanan VARCHAR(80) NOT NULL,
    quotation_id BIGINT REFERENCES quotation(id),
    klien_id BIGINT NOT NULL REFERENCES klien(id),
    tanggal_pesanan DATE NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'DIPROSES',
    status_pembayaran VARCHAR(30) NOT NULL DEFAULT 'BELUM_DIBAYAR',
    total NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total >= 0),
    catatan TEXT,
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    dibuat_oleh BIGINT REFERENCES pengguna(id),
    UNIQUE (perusahaan_id, nomor_pesanan)
);

CREATE TABLE pesanan_penjualan_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pesanan_id BIGINT NOT NULL REFERENCES pesanan_penjualan(id),
    quotation_item_id BIGINT REFERENCES quotation_item(id),
    model_alat_id BIGINT REFERENCES model_alat(id),
    deskripsi_snapshot TEXT NOT NULL,
    kuantitas NUMERIC(12,2) NOT NULL CHECK (kuantitas > 0),
    harga_satuan NUMERIC(18,2) NOT NULL CHECK (harga_satuan >= 0),
    subtotal NUMERIC(18,2) NOT NULL CHECK (subtotal >= 0)
);

CREATE TABLE serah_terima_penjualan (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pesanan_id BIGINT NOT NULL REFERENCES pesanan_penjualan(id),
    tanggal TIMESTAMPTZ NOT NULL,
    diserahkan_oleh BIGINT NOT NULL REFERENCES pengguna(id),
    diterima_oleh VARCHAR(180) NOT NULL,
    bukti_url TEXT,
    catatan TEXT
);

CREATE TABLE serah_terima_penjualan_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    serah_terima_id BIGINT NOT NULL REFERENCES serah_terima_penjualan(id),
    pesanan_item_id BIGINT NOT NULL REFERENCES pesanan_penjualan_item(id),
    unit_alat_id BIGINT REFERENCES unit_alat(id),
    kuantitas NUMERIC(12,2) NOT NULL CHECK (kuantitas > 0)
);

CREATE TABLE pergerakan_stok (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    perusahaan_id BIGINT NOT NULL REFERENCES perusahaan(id),
    model_alat_id BIGINT NOT NULL REFERENCES model_alat(id),
    waktu TIMESTAMPTZ NOT NULL DEFAULT now(),
    jenis VARCHAR(20) NOT NULL CHECK (jenis IN ('MASUK','KELUAR','PENYESUAIAN')),
    kuantitas NUMERIC(12,2) NOT NULL CHECK (kuantitas > 0),
    referensi_tipe VARCHAR(40),
    referensi_id BIGINT,
    dicatat_oleh BIGINT NOT NULL REFERENCES pengguna(id),
    catatan TEXT
);

CREATE TABLE rental (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    perusahaan_id BIGINT NOT NULL REFERENCES perusahaan(id),
    nomor_rental VARCHAR(80) NOT NULL,
    quotation_id BIGINT REFERENCES quotation(id),
    invoice_id BIGINT UNIQUE REFERENCES invoice(id),
    klien_id BIGINT NOT NULL REFERENCES klien(id),
    tanggal_mulai_rencana DATE NOT NULL,
    tanggal_selesai_rencana DATE NOT NULL,
    tanggal_mulai_aktual DATE,
    tanggal_selesai_aktual DATE,
    lokasi_penggunaan TEXT,
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    status VARCHAR(30) NOT NULL DEFAULT 'DIPROSES',
    status_pembayaran VARCHAR(30) NOT NULL DEFAULT 'BELUM_DIBAYAR',
    total NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (total >= 0),
    catatan TEXT,
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    dibuat_oleh BIGINT REFERENCES pengguna(id),
    UNIQUE (perusahaan_id, nomor_rental),
    CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    CHECK (tanggal_selesai_rencana >= tanggal_mulai_rencana),
    CHECK (tanggal_selesai_aktual IS NULL OR tanggal_mulai_aktual IS NULL OR tanggal_selesai_aktual >= tanggal_mulai_aktual)
);

CREATE TABLE rental_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rental_id BIGINT NOT NULL REFERENCES rental(id),
    quotation_item_id BIGINT REFERENCES quotation_item(id),
    model_alat_id BIGINT REFERENCES model_alat(id),
    unit_alat_id BIGINT REFERENCES unit_alat(id),
    paket_unit_id BIGINT REFERENCES paket_unit(id),
    kuantitas NUMERIC(12,2) NOT NULL DEFAULT 1 CHECK (kuantitas > 0),
    tarif NUMERIC(18,2) NOT NULL DEFAULT 0 CHECK (tarif >= 0),
    satuan_tarif VARCHAR(30) NOT NULL DEFAULT 'HARI',
    kondisi_keluar VARCHAR(40),
    kondisi_kembali VARCHAR(40)
);

CREATE TABLE serah_terima_rental (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rental_id BIGINT NOT NULL REFERENCES rental(id),
    jenis VARCHAR(20) NOT NULL CHECK (jenis IN ('KELUAR','KEMBALI')),
    tanggal TIMESTAMPTZ NOT NULL,
    pic_internal_id BIGINT NOT NULL REFERENCES pengguna(id),
    penerima_nama VARCHAR(180),
    bukti_url TEXT,
    catatan TEXT
);

CREATE TABLE serah_terima_rental_item (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    serah_terima_id BIGINT NOT NULL REFERENCES serah_terima_rental(id),
    rental_item_id BIGINT NOT NULL REFERENCES rental_item(id),
    paket_alat_item_id BIGINT REFERENCES paket_alat_item(id),
    unit_alat_id BIGINT REFERENCES unit_alat(id),
    jumlah NUMERIC(12,2) NOT NULL DEFAULT 1 CHECK (jumlah > 0),
    kondisi VARCHAR(40),
    pembacaan_meter NUMERIC(14,2),
    catatan TEXT
);

CREATE TABLE proyek (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    perusahaan_id BIGINT NOT NULL REFERENCES perusahaan(id),
    kode_proyek VARCHAR(80) NOT NULL,
    nama VARCHAR(200) NOT NULL,
    klien_id BIGINT NOT NULL REFERENCES klien(id),
    quotation_id BIGINT REFERENCES quotation(id),
    jenis_layanan VARCHAR(100) NOT NULL,
    lokasi_id BIGINT REFERENCES lokasi(id),
    lokasi_teks TEXT,
    latitude NUMERIC(10,7),
    longitude NUMERIC(10,7),
    pic_internal_id BIGINT REFERENCES pengguna(id),
    kontak_klien_id BIGINT REFERENCES kontak_klien(id),
    tanggal_mulai_rencana DATE,
    tanggal_selesai_rencana DATE,
    tanggal_mulai_aktual DATE,
    tanggal_selesai_aktual DATE,
    status VARCHAR(30) NOT NULL DEFAULT 'DIRENCANAKAN',
    status_laporan VARCHAR(30),
    laporan_url TEXT,
    catatan TEXT,
    dibuat_pada TIMESTAMPTZ NOT NULL DEFAULT now(),
    dibuat_oleh BIGINT REFERENCES pengguna(id),
    UNIQUE (perusahaan_id, kode_proyek),
    CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90),
    CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180),
    CHECK (tanggal_selesai_rencana IS NULL OR tanggal_mulai_rencana IS NULL OR tanggal_selesai_rencana >= tanggal_mulai_rencana),
    CHECK (tanggal_selesai_aktual IS NULL OR tanggal_mulai_aktual IS NULL OR tanggal_selesai_aktual >= tanggal_mulai_aktual)
);

CREATE TABLE proyek_personel (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proyek_id BIGINT NOT NULL REFERENCES proyek(id),
    pengguna_id BIGINT NOT NULL REFERENCES pengguna(id),
    tugas VARCHAR(120),
    mulai_pada DATE,
    selesai_pada DATE,
    CHECK (selesai_pada IS NULL OR mulai_pada IS NULL OR selesai_pada >= mulai_pada)
);

CREATE TABLE proyek_unit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    proyek_id BIGINT NOT NULL REFERENCES proyek(id),
    unit_alat_id BIGINT NOT NULL REFERENCES unit_alat(id),
    paket_unit_id BIGINT REFERENCES paket_unit(id),
    mulai_rencana DATE,
    selesai_rencana DATE,
    mulai_aktual DATE,
    selesai_aktual DATE,
    kondisi_keluar VARCHAR(40),
    kondisi_kembali VARCHAR(40),
    catatan TEXT,
    CHECK (selesai_rencana IS NULL OR mulai_rencana IS NULL OR selesai_rencana >= mulai_rencana),
    CHECK (selesai_aktual IS NULL OR mulai_aktual IS NULL OR selesai_aktual >= mulai_aktual)
);

CREATE TABLE perawatan (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unit_alat_id BIGINT NOT NULL REFERENCES unit_alat(id),
    jenis VARCHAR(50) NOT NULL,
    tanggal_mulai DATE NOT NULL,
    tanggal_selesai DATE,
    vendor VARCHAR(180),
    biaya NUMERIC(18,2) CHECK (biaya IS NULL OR biaya >= 0),
    hasil TEXT,
    kalibrasi_berlaku_sampai DATE,
    dokumen_url TEXT,
    catatan TEXT,
    CHECK (tanggal_selesai IS NULL OR tanggal_selesai >= tanggal_mulai)
);

-- Kalender ketersediaan bersama untuk semua transaksi QR dan SNV.
-- Rentang tanggal memakai batas akhir eksklusif: [2026-01-01, 2026-01-04)
-- berarti alat dipakai 1-3 Januari dan tersedia kembali pada 4 Januari.
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE jadwal_unit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unit_alat_id BIGINT NOT NULL REFERENCES unit_alat(id),
    periode DATERANGE NOT NULL,
    jenis VARCHAR(20) NOT NULL CHECK (jenis IN ('RENTAL','PROYEK','PERAWATAN')),
    status VARCHAR(20) NOT NULL DEFAULT 'DIPESAN' CHECK (status IN ('DIPESAN','AKTIF','SELESAI','DIBATALKAN')),
    rental_item_id BIGINT REFERENCES rental_item(id),
    proyek_unit_id BIGINT REFERENCES proyek_unit(id),
    perawatan_id BIGINT REFERENCES perawatan(id),
    perusahaan_transaksi_id BIGINT REFERENCES perusahaan(id),
    catatan TEXT,
    CHECK (NOT isempty(periode)),
    CHECK (
        (jenis = 'RENTAL' AND rental_item_id IS NOT NULL AND proyek_unit_id IS NULL AND perawatan_id IS NULL)
        OR (jenis = 'PROYEK' AND rental_item_id IS NULL AND proyek_unit_id IS NOT NULL AND perawatan_id IS NULL)
        OR (jenis = 'PERAWATAN' AND rental_item_id IS NULL AND proyek_unit_id IS NULL AND perawatan_id IS NOT NULL)
    ),
    EXCLUDE USING gist (
        unit_alat_id WITH =,
        periode WITH &&
    ) WHERE (status IN ('DIPESAN','AKTIF'))
);

CREATE TABLE riwayat_unit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    unit_alat_id BIGINT NOT NULL REFERENCES unit_alat(id),
    jenis VARCHAR(20) NOT NULL CHECK (jenis IN ('STATUS','LOKASI')),
    nilai_lama TEXT,
    nilai_baru TEXT NOT NULL,
    waktu TIMESTAMPTZ NOT NULL DEFAULT now(),
    referensi_tipe VARCHAR(40),
    referensi_id BIGINT,
    dicatat_oleh BIGINT REFERENCES pengguna(id),
    catatan TEXT
);

-- Audit perubahan untuk transaksi dan master data.
-- Aplikasi menulis satu baris untuk setiap perubahan penting; baris audit tidak diedit/dihapus.
CREATE TABLE audit_perubahan (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    jenis_entitas VARCHAR(60) NOT NULL,
    entitas_id BIGINT NOT NULL,
    aksi VARCHAR(30) NOT NULL CHECK (aksi IN ('BUAT','UBAH','HAPUS_LOGIS','UBAH_STATUS','PERSETUJUAN')),
    waktu TIMESTAMPTZ NOT NULL DEFAULT now(),
    pengguna_id BIGINT REFERENCES pengguna(id),
    alasan TEXT,
    nilai_sebelum JSONB,
    nilai_sesudah JSONB,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_quotation_perusahaan_tanggal ON quotation (perusahaan_id, tanggal DESC);
CREATE INDEX idx_quotation_klien ON quotation (klien_id);
CREATE INDEX idx_unit_status_lokasi ON unit_alat (status, lokasi_id);
CREATE INDEX idx_rental_status_tanggal ON rental (status, tanggal_selesai_rencana);
CREATE INDEX idx_proyek_perusahaan_status ON proyek (perusahaan_id, status);
CREATE INDEX idx_riwayat_unit_waktu ON riwayat_unit (unit_alat_id, waktu DESC);
CREATE INDEX idx_audit_entitas_waktu ON audit_perubahan (jenis_entitas, entitas_id, waktu DESC);
CREATE INDEX idx_audit_pengguna_waktu ON audit_perubahan (pengguna_id, waktu DESC);

COMMIT;
