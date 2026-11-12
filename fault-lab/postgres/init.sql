-- Lab database roles. Idempotent: it runs at first initialisation and again
-- on every `make lab-up` through the lab-db-bootstrap job, because Docker
-- Compose keeps the data volume of a recreated lab-postgres container and
-- initdb scripts then never run again.

-- Read-only monitoring identity for postgres_exporter (pg_monitor grants
-- access to pg_stat_* views without data access or write privileges). It may
-- use the reserved connection slots, so the exporter keeps reporting even
-- when every ordinary slot is taken (B-F12).
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lab_monitor') THEN
    CREATE ROLE lab_monitor LOGIN PASSWORD 'lab_monitor';
  END IF;
  -- Ordinary (non-superuser) application role used by lab-api. Without the
  -- superuser's reserved slots it is refused once all ordinary connection
  -- slots are held, which is what an exhausted connection budget looks like.
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'lab_app') THEN
    CREATE ROLE lab_app LOGIN PASSWORD 'lab_app';
  END IF;
END
$$;
GRANT pg_monitor, pg_use_reserved_connections TO lab_monitor;
-- Monitoring must never queue behind an injected lock: fail fast instead so the
-- exporter keeps reporting while the lab database is contended.
ALTER ROLE lab_monitor SET lock_timeout = '1s';
ALTER ROLE lab_monitor SET statement_timeout = '3s';

ALTER ROLE lab_app NOSUPERUSER;
GRANT CREATE, USAGE ON SCHEMA public TO lab_app;
-- Tables created by the superuser lab (demo_events by the consumer, and
-- lab_items on databases initialised before lab_app existed).
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO lab_app;
ALTER DEFAULT PRIVILEGES FOR ROLE lab IN SCHEMA public GRANT SELECT ON TABLES TO lab_app;
