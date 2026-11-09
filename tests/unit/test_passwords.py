"""Tests for scrypt password hashing."""

import pytest

from incidentops.auth.passwords import hash_password, verify_password


def test_round_trip_and_unique_salts() -> None:
    first = hash_password("correct horse battery")
    second = hash_password("correct horse battery")
    assert first != second
    assert verify_password("correct horse battery", first)
    assert verify_password("correct horse battery", second)


def test_wrong_password_and_malformed_hashes_fail() -> None:
    encoded = hash_password("correct horse battery")
    assert not verify_password("wrong horse battery!", encoded)
    assert not verify_password("correct horse battery", "md5$abc")
    assert not verify_password("correct horse battery", "scrypt$x$y$z$a$b")
    assert not verify_password("correct horse battery", encoded.replace("scrypt", "bcrypt"))


def test_short_passwords_rejected() -> None:
    with pytest.raises(ValueError, match="at least"):
        hash_password("short")


def test_plaintext_not_in_hash() -> None:
    assert "correct horse" not in hash_password("correct horse battery")
