"""Helpers for building per-purpose database connection strings."""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from incidentops.persistence.privileges import LANGGRAPH_SCHEMA


def checkpoint_conninfo(sqlalchemy_url: str) -> str:
    """psycopg URL for the LangGraph checkpointer, pinned to its own schema.

    The agent role may create tables only in ``langgraph`` (migration 0004), so
    the checkpointer's unqualified table names must resolve there.
    """
    parts = urlsplit(sqlalchemy_url.replace("+psycopg", ""))
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "options"]
    query.append(("options", f"-csearch_path={LANGGRAPH_SCHEMA}"))
    return urlunsplit(parts._replace(query=urlencode(query)))
