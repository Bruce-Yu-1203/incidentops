-- Read-only monitoring identity for postgres_exporter (pg_monitor grants
-- access to pg_stat_* views without data access or write privileges).
CREATE ROLE lab_monitor LOGIN PASSWORD 'lab_monitor' IN ROLE pg_monitor;
