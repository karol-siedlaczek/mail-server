#!/usr/bin/env bash
# wait-postgres.sh — best-effort readiness poll for the external PostgreSQL.
#
# Run by the postgres-ready s6 oneshot before audit-svc starts, to avoid a burst
# of reconnect noise on first boot / compose-up. The DB is EXTERNAL and operated
# independently, so this is deliberately best-effort: on timeout we log and exit
# 0 rather than fail, so a transient DB blip never aborts the whole mail server
# (audit-svc reconnects on its own). Uses pg_isready (postgresql-client is in
# the image); nc is not installed.
set -u
PG_HOST="${PG_HOST:-localhost}"
PG_PORT="${PG_PORT:-5432}"
PG_USER="${PG_USER:-postgres}"
PG_DBNAME="${PG_DBNAME:-postgres}"
TIMEOUT="${PG_WAIT_TIMEOUT:-60}"

i=0
while [ "$i" -lt "$TIMEOUT" ]; do
    if pg_isready -h "$PG_HOST" -p "$PG_PORT" -U "$PG_USER" -d "$PG_DBNAME" -q; then
        echo "postgres-ready: ${PG_HOST}:${PG_PORT} accepting connections" >&2
        exit 0
    fi
    i=$((i + 1))
    sleep 1
done
echo "postgres-ready: ${PG_HOST}:${PG_PORT} not ready after ${TIMEOUT}s; proceeding anyway (audit-svc will retry)" >&2
exit 0
