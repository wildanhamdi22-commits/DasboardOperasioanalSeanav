BEGIN;

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

ALTER TABLE rental
    ADD COLUMN invoice_id BIGINT UNIQUE REFERENCES invoice(id);

COMMIT;
