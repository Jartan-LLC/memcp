#!/bin/bash
# Creates the app database mem0's auth tables live in. pgvector uses the default
# database for memory storage. Mirrors server/init-db.sh in Jartan-LLC/mem0.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE mem0_app'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'mem0_app')\gexec
EOSQL
