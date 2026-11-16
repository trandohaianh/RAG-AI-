#!/bin/sh
set -eu
password=$(cat /run/secrets/rag_db_password)
printf '%s' "$password" | grep -Eq '^[0-9a-f]{64}$' || exit 64
# Generated hex only; password never appears in process arguments or logs.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<EOSQL
CREATE USER rag_app WITH PASSWORD '$password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE rag OWNER rag_app;
EOSQL
