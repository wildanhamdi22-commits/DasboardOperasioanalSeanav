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
    ('Geodetic', 'Peralatan geodesi & positioning'),
    ('Geophysic', 'Peralatan geofisika'),
    ('Metocean', 'Peralatan meteorologi & oseanografi')
ON CONFLICT (nama) DO NOTHING;
-- Daftar alat diinput sendiri melalui menu Rental > Daftar Alat dan Penjualan > Katalog.

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
