#!/usr/bin/env bash
# Throwaway local PostgreSQL cluster for development and the test-suite (no Docker needed).
# Usage: scripts/pg_local.sh {start|stop|status|dsn}
# Needs PostgreSQL server binaries (e.g. /usr/lib/postgresql/16/bin). Runs as the 'postgres' OS user when invoked as root.
set -euo pipefail

PGBIN="${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
PGDATA="${PGDATA:-/tmp/aridtrial_pg}"
PGPORT="${PGPORT:-5433}"
SOCK="${PGSOCK:-/tmp}"

run() { if [ "$(id -u)" = "0" ]; then runuser -u postgres -- "$@"; else "$@"; fi; }

case "${1:-start}" in
  start)
    [ -x "$PGBIN/initdb" ] || { echo "PostgreSQL server binaries not found (set PGBIN)" >&2; exit 2; }
    if [ ! -d "$PGDATA/base" ]; then
      mkdir -p "$PGDATA"; [ "$(id -u)" = "0" ] && chown postgres:postgres "$PGDATA"
      run "$PGBIN/initdb" -D "$PGDATA" -A trust -U postgres >/dev/null
    fi
    if ! run "$PGBIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; then
      run "$PGBIN/pg_ctl" -D "$PGDATA" -o "-p $PGPORT -k $SOCK -c listen_addresses=127.0.0.1" -l "$PGDATA/server.log" -w start >/dev/null
    fi
    run "$PGBIN/psql" -h "$SOCK" -p "$PGPORT" -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='aridtrial'" | grep -q 1 \
      || run "$PGBIN/psql" -h "$SOCK" -p "$PGPORT" -U postgres -qc "CREATE ROLE aridtrial LOGIN PASSWORD 'aridtrial'"
    run "$PGBIN/psql" -h "$SOCK" -p "$PGPORT" -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='aridtrial'" | grep -q 1 \
      || run "$PGBIN/createdb" -h "$SOCK" -p "$PGPORT" -U postgres -O aridtrial aridtrial
    run "$PGBIN/psql" -h "$SOCK" -p "$PGPORT" -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='aridtrial_test'" | grep -q 1 \
      || run "$PGBIN/createdb" -h "$SOCK" -p "$PGPORT" -U postgres -O aridtrial aridtrial_test
    run "$PGBIN/psql" -h "$SOCK" -p "$PGPORT" -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='aridtrial_test2'" | grep -q 1 \
      || run "$PGBIN/createdb" -h "$SOCK" -p "$PGPORT" -U postgres -O aridtrial aridtrial_test2
    echo "ready: postgresql+psycopg2://aridtrial:aridtrial@127.0.0.1:$PGPORT/aridtrial"
    ;;
  stop)   run "$PGBIN/pg_ctl" -D "$PGDATA" -m fast stop ;;
  status) run "$PGBIN/pg_ctl" -D "$PGDATA" status ;;
  dsn)    echo "postgresql+psycopg2://aridtrial:aridtrial@127.0.0.1:$PGPORT/aridtrial" ;;
  *) echo "usage: $0 {start|stop|status|dsn}" >&2; exit 1 ;;
esac
