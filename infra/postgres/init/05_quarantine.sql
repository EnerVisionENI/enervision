-- infra/postgres/init/05_quarantine.sql
-- Réplique Postgres du bucket MinIO "quarantine" (etl/quality.py -> write_quarantine).
--
-- La quarantaine était écrite en petits objets JSON, un par rejet, sous la date de
-- TRAITEMENT : inexploitable — il fallait lister puis GET des milliers d'objets pour
-- répondre à « quels sites rejettent, et quand », et un rejet ne pouvait pas être rapproché
-- du trou qu'il laisse dans le gold. Elle est désormais partitionnée sur la date de la
-- MESURE, comme silver et gold, en Parquet côté MinIO et dans cette table côté SQL.
--
-- Ne contient que les rejets STRUCTURELS : JSON illisible, champ obligatoire absent,
-- horodatage invalide. Une lecture bien formée mais vide (critical / network_loss) n'est
-- pas un rejet : elle reste en silver avec is_valid = false, pour que le trou soit daté.
--
-- Pas de clé étrangère vers sites(site_id) : un enregistrement rejeté peut justement porter
-- un site_id inconnu ou illisible, c'est parfois la raison même du rejet.

CREATE TABLE IF NOT EXISTS measurements_quarantine (
    source_key     VARCHAR(255) PRIMARY KEY,
    error_type     VARCHAR(50) NOT NULL,
    error_message  TEXT,
    site_id        VARCHAR(20),
    record_date    DATE,
    raw_timestamp  TEXT,
    raw_record     TEXT,
    captured_at    TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_quarantine_site_date ON measurements_quarantine(site_id, record_date);
CREATE INDEX IF NOT EXISTS idx_quarantine_error_type ON measurements_quarantine(error_type);
