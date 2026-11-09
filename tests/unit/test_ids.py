"""Tests for prefixed identifiers."""

import pytest

from incidentops.domain.ids import id_prefix, new_id


def test_new_id_round_trips_prefix() -> None:
    value = new_id("inc")
    assert id_prefix(value) == "inc"


def test_new_ids_are_unique() -> None:
    assert len({new_id("act") for _ in range(1000)}) == 1000


def test_unknown_prefix_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown id prefix"):
        new_id("shell")


@pytest.mark.parametrize("value", ["inc_123", "zzz_" + "0" * 32, "inc-" + "0" * 32, ""])
def test_malformed_ids_are_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="malformed"):
        id_prefix(value)
