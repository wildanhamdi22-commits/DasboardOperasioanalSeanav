-- Data master awal Seanav Operations, disusun dari dokumen quotation/invoice/DO di folder Database.
-- Hanya berisi data yang tertulis di dokumen. Nomor seri unit alat TIDAK ada di dokumen,
-- jadi unit alat ditambahkan sendiri lewat menu Inventaris.
-- Aman dijalankan ulang: setiap baris dilewati bila sudah ada.
BEGIN;

-- Lokasi gudang (dokumen: alat tersedia "ex premises in Jakarta")
INSERT INTO lokasi (nama, jenis)
SELECT 'Gudang Jakarta', 'GUDANG'
WHERE NOT EXISTS (SELECT 1 FROM lokasi WHERE nama = 'Gudang Jakarta');

-- Kategori alat
INSERT INTO kategori_alat (nama, deskripsi) VALUES
    ('Multibeam Echosounder', 'Pemetaan batimetri multibeam'),
    ('Sonar & FMD', 'Sonar inspeksi dan flooded member detection'),
    ('Tide Gauge', 'Pengukur pasang surut'),
    ('Magnetometer', 'Marine magnetometer untuk geofisika & UXO'),
    ('Drone & LiDAR', 'UAV dan sensor LiDAR'),
    ('GNSS', 'Receiver GNSS / RTK'),
    ('ROV', 'Remotely operated vehicle')
ON CONFLICT (nama) DO NOTHING;

-- Model alat (merek & model dari dokumen; spesifikasi dari quotation SNV/26061/IX/2026)
INSERT INTO model_alat (kategori_alat_id, merek, nama_model, deskripsi, spesifikasi)
SELECT k.id, v.merek, v.model, v.deskripsi, v.spek::jsonb
FROM (VALUES
    ('Multibeam Echosounder', 'R2Sonic', '2020', 'Multibeam echosounder, lengkap kabel & interface unit',
     '{"frekuensi":"200–450 kHz (700 kHz opsional)","kedalaman":"200 m+","soundings_per_ping":"hingga 1.024","sektor_swath":"10°–130°","ping_rate":"hingga 60 Hz","resolusi_deteksi":"3 mm"}'),
    ('Multibeam Echosounder', 'Norbit', 'iWBMS X', 'Wideband multibeam terintegrasi GNSS/INS', '{}'),
    ('Sonar & FMD', 'Impact Subsea', 'ISA500', 'Flooded member detection (FMD)',
     '{"frekuensi":"500 kHz","rentang":"0,1–30 m","depth_rating":"6.000 m","resolusi":"1 mm","beam_angle":"6° conical"}'),
    ('Tide Gauge', 'Valeport', 'TideMaster', 'Tide gauge sensor pressure / radar', '{}'),
    ('Magnetometer', 'Geometrics', 'G-882', 'Marine cesium magnetometer', '{}'),
    ('Drone & LiDAR', 'DJI', 'Matrice 400', 'Drone platform untuk survei LiDAR', '{}'),
    ('Drone & LiDAR', 'DJI', 'Zenmuse L3', 'Sensor LiDAR untuk Matrice 400', '{}'),
    ('GNSS', 'DJI', 'D-RTK 2', 'Mobile base station RTK', '{}'),
    ('GNSS', 'Trimble', 'R8s', 'GNSS receiver RTK / static', '{}'),
    ('ROV', 'Saab Seaeye', 'Falcon', 'ROV inspeksi bawah air', '{}')
) AS v(kategori, merek, model, deskripsi, spek)
JOIN kategori_alat k ON k.nama = v.kategori
ON CONFLICT (kategori_alat_id, merek, nama_model) DO NOTHING;

-- Klien (dari nama file dan isi quotation/invoice)
INSERT INTO klien (nama_perusahaan, alamat)
SELECT v.nama, v.alamat
FROM (VALUES
    ('PT Advanced Offshore Services',
     'Gedung Ventura Lantai 6 / 601, Jl. R.A. Kartini No.26, RT.12/RW.6, Cilandak Barat, Kec. Cilandak, Kota Jakarta Selatan, DKI Jakarta 12430'),
    ('DIGIKO', NULL),
    ('PT Taka Hydrocore Indonesia', NULL),
    ('PT Samudera', NULL),
    ('PT Agrapana Nugraha Katara', NULL)
) AS v(nama, alamat)
WHERE NOT EXISTS (SELECT 1 FROM klien k WHERE lower(k.nama_perusahaan) = lower(v.nama));

-- Kontak PIC klien (P.O.C. di quotation)
INSERT INTO kontak_klien (klien_id, nama, utama)
SELECT k.id, v.nama, TRUE
FROM (VALUES ('PT Advanced Offshore Services', 'Bapak Ferdy'), ('DIGIKO', 'Bapak Miko')) AS v(klien, nama)
JOIN klien k ON k.nama_perusahaan = v.klien
WHERE NOT EXISTS (SELECT 1 FROM kontak_klien kk WHERE kk.klien_id = k.id AND kk.nama = v.nama);

-- Staf BD yang tercantum di quotation (login tetap lewat akun Django dengan email yang sama)
INSERT INTO pengguna (nama, email, password_hash)
VALUES ('Siska', 'siska@seanavgroup.co.id', '!'), ('Farid', 'farid@seanavgroup.co.id', '!')
ON CONFLICT (email) DO NOTHING;

COMMIT;
