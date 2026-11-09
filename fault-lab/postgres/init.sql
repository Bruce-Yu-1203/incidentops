-- Read-only monitoring identity for postgres_exporter (pg_monitor grants
-- access to pg_stat_* views without data access or write privileges).
CREATE ROLE lab_monitor LOGIN PASSWORD 'lab_monitor' IN ROLE pg_monitor;
-- Monitoring must never queue behind an injected lock: fail fast instead so the
-- exporter keeps reporting while the lab database is contended.
ALTER ROLE lab_monitor SET lock_timeout = '1s';
ALTER ROLE lab_monitor SET statement_timeout = '3s';
