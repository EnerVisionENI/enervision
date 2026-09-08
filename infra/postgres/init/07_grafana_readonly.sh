#!/bin/sh
# infra/postgres/init/07_grafana_readonly.sh
# Rôle de lecture seule utilisé par la datasource PostgreSQL de Grafana
# (infra/grafana/provisioning/datasources/datasource.yml).
#
# Pourquoi un rôle dédié et pas POSTGRES_USER : Grafana exécute du SQL écrit
# dans des dashboards éditables depuis l'interface (allowUiUpdates: true). Le
# jour où quelqu'un colle un DELETE dans un panneau, la seule barrière est le
# rôle. SELECT-only sur public, aucun droit d'écriture, aucun droit DDL.
#
# En .sh et non en .sql parce que le mot de passe vient de l'environnement du
# conteneur : un .sql du répertoire d'init ne peut pas le lire, et l'écrire en
# dur dans un fichier versionné est exclu.
#
# Sur un volume déjà initialisé — c'est le cas du serveur, infra_pgdata vient de
# l'ancien projet Compose — les scripts de ce répertoire ne rejouent pas. Il
# faut donc l'appliquer une fois à la main, après avoir renseigné
# GRAFANA_DB_PASSWORD dans .env et recréé le conteneur postgres :
#
#   docker compose up -d postgres
#   docker compose exec postgres sh /docker-entrypoint-initdb.d/07_grafana_readonly.sh
#
# Le script est idempotent : il crée le rôle s'il manque, et réaligne son mot de
# passe et ses droits à chaque passage.
#
# Ni `set -e` ni `exit` : l'entrypoint Postgres source les .sh non exécutables,
# donc les deux couperaient l'initialisation du serveur lui-même.

if [ -z "${GRAFANA_DB_PASSWORD:-}" ]; then
  echo "07_grafana_readonly : GRAFANA_DB_PASSWORD absent, rôle de lecture non créé."
  echo "07_grafana_readonly : la datasource Grafana ne pourra pas se connecter tant qu'il manque."
else
  psql -v ON_ERROR_STOP=1 \
       --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
       -v ro_user="${GRAFANA_DB_USER:-grafana_ro}" \
       -v ro_pwd="$GRAFANA_DB_PASSWORD" \
       -v db_name="$POSTGRES_DB" <<-'EOSQL'
	-- format(%I/%L) plutôt qu'une interpolation directe : le nom de rôle et le mot
	-- de passe viennent de l'environnement, ils sont donc échappés comme
	-- identifiant et comme littéral, pas concaténés.
	SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'ro_user', :'ro_pwd')
	WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'ro_user')
	\gexec

	-- Réalignement du mot de passe : rend le script rejouable après une rotation
	-- de secret, sans avoir à supprimer le rôle et ses droits.
	SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'ro_user', :'ro_pwd')
	\gexec

	GRANT CONNECT ON DATABASE :"db_name" TO :"ro_user";
	GRANT USAGE  ON SCHEMA public        TO :"ro_user";
	GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"ro_user";

	-- Les tables créées plus tard par ev_admin (nouvelle table de prédictions,
	-- nouvel agrégat) seraient sinon invisibles de Grafana jusqu'à un GRANT manuel.
	ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"ro_user";
	EOSQL
  echo "07_grafana_readonly : rôle ${GRAFANA_DB_USER:-grafana_ro} aligné (SELECT seul sur public)."
fi
