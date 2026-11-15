-- Tambahkan metadata yang ada pada daftar inventaris manual.
-- Jalankan satu kali pada database pengembangan yang skema_awal.sql-nya sudah diterapkan.
BEGIN;

ALTER TABLE unit_alat
    ADD COLUMN om_sn VARCHAR(120),
    ADD COLUMN foto_url TEXT;

COMMIT;
