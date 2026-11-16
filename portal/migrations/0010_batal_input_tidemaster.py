"""Hapus unit Valeport TideMaster S/N 54593 yang sempat dibuat otomatis oleh migrasi 0009.

Hanya menghapus unit yang dibuat otomatis (catatan khusus) dan belum pernah dipakai di transaksi.
"""
from django.db import migrations

SQL = """
DO $$
DECLARE u_id BIGINT; p_id BIGINT; pa_id BIGINT;
BEGIN
  SELECT id INTO u_id FROM unit_alat
   WHERE nomor_seri = '54593' AND catatan = 'Tide Master Pressure Valeport (Teledyne)'
     AND NOT EXISTS (SELECT 1 FROM rental_item ri WHERE ri.unit_alat_id = unit_alat.id)
   LIMIT 1;
  IF u_id IS NULL THEN RETURN; END IF;
  SELECT id, paket_alat_id INTO p_id, pa_id FROM paket_unit WHERE unit_utama_id = u_id;
  IF p_id IS NOT NULL THEN
    DELETE FROM paket_unit_item WHERE paket_unit_id = p_id;
    DELETE FROM paket_unit WHERE id = p_id;
    DELETE FROM paket_alat_item WHERE paket_alat_id = pa_id;
    DELETE FROM paket_alat WHERE id = pa_id;
  END IF;
  DELETE FROM riwayat_unit WHERE unit_alat_id = u_id;
  DELETE FROM jadwal_unit WHERE unit_alat_id = u_id;
  DELETE FROM unit_alat WHERE id = u_id;
  DELETE FROM model_alat m WHERE m.merek = 'Valeport' AND m.nama_model = 'TideMaster'
     AND NOT EXISTS (SELECT 1 FROM unit_alat x WHERE x.model_alat_id = m.id)
     AND NOT EXISTS (SELECT 1 FROM quotation_item q WHERE q.model_alat_id = m.id);
EXCEPTION WHEN foreign_key_violation THEN
  RAISE NOTICE 'Unit TideMaster sudah dipakai; tidak dihapus.';
END $$;
"""


class Migration(migrations.Migration):
    dependencies = [("portal", "0009_alat_valeport_tidemaster")]
    operations = [migrations.RunSQL(SQL, migrations.RunSQL.noop)]
