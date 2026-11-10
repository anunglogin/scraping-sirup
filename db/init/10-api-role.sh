#!/bin/bash
# Runs once, when the database volume is first initialised
# (docker-entrypoint-initdb.d). Creates the READ-ONLY role used by the API.
set -euo pipefail

: "${API_DB_USER:?API_DB_USER is required}"
: "${API_DB_PASSWORD:?API_DB_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v api_user="$API_DB_USER" \
  -v api_password="$API_DB_PASSWORD" \
  -v db_name="$POSTGRES_DB" \
  -v owner="$POSTGRES_USER" <<'EOSQL'
CREATE ROLE :"api_user" LOGIN PASSWORD :'api_password';
ALTER ROLE :"api_user" SET default_transaction_read_only = on;
GRANT CONNECT ON DATABASE :"db_name" TO :"api_user";
GRANT USAGE ON SCHEMA public TO :"api_user";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"api_user";
-- Tables created later by migrations (run as the owner) become readable too.
ALTER DEFAULT PRIVILEGES FOR ROLE :"owner" IN SCHEMA public
  GRANT SELECT ON TABLES TO :"api_user";
EOSQL

echo "read-only role '$API_DB_USER' created"
