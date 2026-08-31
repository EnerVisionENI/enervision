-- infra/postgres/init.sql

-- Extension nécessaire pour gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS sites (
    site_id      VARCHAR(20) PRIMARY KEY,
    site_type    VARCHAR(50),
    site_name    VARCHAR(100),
    location     VARCHAR(100),
    capacity_kw  NUMERIC,
    status       VARCHAR(20)
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id   VARCHAR(50) PRIMARY KEY,
    timestamp  TIMESTAMP,
    site_id    VARCHAR(20) REFERENCES sites(site_id),
    severity   VARCHAR(20),
    type       VARCHAR(30),
    message    TEXT,
    value      NUMERIC,
    threshold  NUMERIC
);

CREATE TABLE IF NOT EXISTS sensors_status (
    site_id       VARCHAR(20) REFERENCES sites(site_id),
    sensor        VARCHAR(30),
    status        VARCHAR(20),
    failing_until TIMESTAMP,
    checked_at    TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS users (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         VARCHAR(150) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    role          VARCHAR(20) NOT NULL DEFAULT 'viewer', -- viewer | operator | admin
    created_at    TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_alerts_site_id ON alerts(site_id);
CREATE INDEX IF NOT EXISTS idx_alerts_timestamp ON alerts(timestamp);
CREATE INDEX IF NOT EXISTS idx_sensors_site_id ON sensors_status(site_id);