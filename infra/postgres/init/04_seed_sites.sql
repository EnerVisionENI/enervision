-- infra/postgres/init/04_seed_sites.sql
-- EV-035 : seed des 7 sites, snapshot de l'API Mock IoT (GET /api/v1/sites) pris le
-- 2026-09-02. C'est aujourd'hui la seule source de la table `sites` : la synchronisation
-- automatique depuis l'API n'existe pas encore. Tant qu'elle n'existe pas, tout site
-- ajouté côté API Mock doit être ajouté ici à la main (voir « Pistes connues » du
-- README racine).
-- Idempotent : ON CONFLICT permet de rejouer ce fichier sans dupliquer les lignes.

INSERT INTO sites (site_id, site_type, site_name, location, capacity_kw, status) VALUES
    ('SITE001', 'office',     'Bureau Paris La Défense', 'Paris, France',    200.0, 'active'),
    ('SITE002', 'factory',    'Usine Lyon Vénissieux',   'Lyon, France',    1000.0, 'active'),
    ('SITE003', 'datacenter', 'Data Center Marseille',   'Marseille, France', 800.0, 'active'),
    ('SITE004', 'retail',     'Centre Commercial Lille', 'Lille, France',    400.0, 'active'),
    ('SITE005', 'hospital',   'Hôpital Toulouse Purpan', 'Toulouse, France', 600.0, 'active'),
    ('SITE006', 'office',     'Bureau Bordeaux Centre',  'Bordeaux, France', 180.0, 'active'),
    ('SITE007', 'factory',    'Usine Nantes Rezé',       'Nantes, France',   950.0, 'active')
ON CONFLICT (site_id) DO UPDATE SET
    site_type   = EXCLUDED.site_type,
    site_name   = EXCLUDED.site_name,
    location    = EXCLUDED.location,
    capacity_kw = EXCLUDED.capacity_kw,
    status      = EXCLUDED.status;
