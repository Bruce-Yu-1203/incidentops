-- Read-only monitoring identity for postgres_exporter (pg_monitor grants
-- access to pg_stat_* views without data access or write privileges).
-- It may use the reserved connection slots, so the exporter keeps reporting
-- even when every ordinary slot is taken (B-F12).
CREATE ROLE lab_monitor LOGIN PASSWORD 'lab_monitor' IN ROLE pg_monitor, pg_use_reserved_connections;
-- Monitoring must never queue behind an injected lock: fail fast instead so the
-- exporter keeps reporting while the lab database is contended.
ALTER ROLE lab_monitor SET lock_timeout = '1s';
ALTER ROLE lab_monitor SET statement_timeout = '3s';

-- Ordinary (non-superuser) application role used by lab-api. Without the
-- superuser's reserved slots it is refused once all ordinary connection slots
-- are held, which is what an exhausted connection budget looks like (B-F12).
CREATE ROLE lab_app LOGIN PASSWORD 'lab_app';
GRANT CREATE, USAGE ON SCHEMA public TO lab_app;
-- The consumer (role lab) creates demo_events; the API counts its rows.
ALTER DEFAULT PRIVILEGES FOR ROLE lab IN SCHEMA public GRANT SELECT ON TABLES TO lab_app;
