import os

import pytest
from fastapi import HTTPException

from api.tools import _validate_http_tool_test_target


def test_validate_http_tool_test_target_blocks_localhost_by_default(monkeypatch):
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", raising=False)
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS", raising=False)

    with pytest.raises(HTTPException) as exc:
        _validate_http_tool_test_target("http://127.0.0.1:8080/test")
    assert exc.value.status_code == 403


def test_validate_http_tool_test_target_allows_localhost_with_override(monkeypatch):
    monkeypatch.setenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", "1")
    monkeypatch.delenv("AAVA_HTTP_TOOL_TEST_ALLOW_HOSTS", raising=False)

    _validate_http_tool_test_target("http://127.0.0.1:8080/test")


def test_validate_http_tool_test_target_rejects_non_http_scheme(monkeypatch):
    monkeypatch.setenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", "1")
    with pytest.raises(HTTPException) as exc:
        _validate_http_tool_test_target("file:///etc/passwd")
    assert exc.value.status_code == 400


def test_validate_http_tool_test_target_rejects_embedded_credentials(monkeypatch):
    monkeypatch.setenv("AAVA_HTTP_TOOL_TEST_ALLOW_PRIVATE", "1")
    with pytest.raises(HTTPException) as exc:
        _validate_http_tool_test_target("http://user:pass@example.com/test")
    assert exc.value.status_code == 400

