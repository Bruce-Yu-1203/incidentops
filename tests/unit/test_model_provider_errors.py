"""Provider error descriptions keep the diagnosis detail but no secrets."""

from __future__ import annotations

import anthropic
import httpx2

from incidentops.agent.model import MAX_PROVIDER_ERROR_BYTES, describe_api_error


def _bad_request(body: object) -> anthropic.BadRequestError:
    request = httpx2.Request("POST", "https://provider.example/v1/messages")
    response = httpx2.Response(400, request=request, json=body)
    return anthropic.BadRequestError("Error code: 400", response=response, body=body)


def test_status_and_body_are_kept() -> None:
    error = _bad_request({"error": {"type": "invalid_request_error", "message": "bad thinking"}})
    detail = describe_api_error(error)
    assert detail.startswith("BadRequestError status=400 body=")
    assert "invalid_request_error" in detail
    assert "bad thinking" in detail


def test_secrets_in_the_body_are_redacted() -> None:
    key = "sk-" + "a1b2c3d4" * 4
    error = _bad_request({"error": {"message": f"invalid key {key}", "auth": "Bearer abc.def"}})
    detail = describe_api_error(error)
    assert key not in detail
    assert "abc.def" not in detail
    assert "[REDACTED]" in detail


def test_long_bodies_are_truncated() -> None:
    detail = describe_api_error(_bad_request({"error": {"message": "x" * 10_000}}))
    assert detail.endswith(" [truncated]")
    assert len(detail.encode("utf-8")) <= MAX_PROVIDER_ERROR_BYTES + len(" [truncated]")


def test_connection_errors_without_a_body_name_the_class() -> None:
    request = httpx2.Request("POST", "https://provider.example/v1/messages")
    detail = describe_api_error(anthropic.APIConnectionError(request=request))
    assert detail.startswith("APIConnectionError")
