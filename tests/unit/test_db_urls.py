"""Tests for per-purpose connection strings."""

from incidentops.persistence.urls import checkpoint_conninfo


def test_checkpoint_conninfo_pins_schema_and_drops_driver() -> None:
    url = checkpoint_conninfo("postgresql+psycopg://io_agent:pw@db:5433/control?sslmode=disable")
    assert url.startswith("postgresql://io_agent:pw@db:5433/control?")
    assert "sslmode=disable" in url
    assert "options=-csearch_path%3Dlanggraph" in url


def test_existing_options_are_replaced() -> None:
    url = checkpoint_conninfo("postgresql://u:p@h/d?options=-csearch_path%3Dpublic")
    assert url.count("options=") == 1
    assert "langgraph" in url
