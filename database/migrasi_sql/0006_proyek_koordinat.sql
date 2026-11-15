BEGIN;

ALTER TABLE proyek
    ADD COLUMN IF NOT EXISTS latitude NUMERIC(10,7),
    ADD COLUMN IF NOT EXISTS longitude NUMERIC(10,7);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'proyek_latitude_range_check'
          AND conrelid = 'proyek'::regclass
    ) THEN
        ALTER TABLE proyek
            ADD CONSTRAINT proyek_latitude_range_check
            CHECK (latitude IS NULL OR latitude BETWEEN -90 AND 90);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'proyek_longitude_range_check'
          AND conrelid = 'proyek'::regclass
    ) THEN
        ALTER TABLE proyek
            ADD CONSTRAINT proyek_longitude_range_check
            CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180);
    END IF;
END $$;

COMMIT;
