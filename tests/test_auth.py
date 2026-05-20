"""Tests for app/core/auth.py — require_api_key dependency."""
import pytest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import require_api_key
from app.core.config import settings

# ---------------------------------------------------------------------------
# Minimal app that applies the auth dependency to a single test route
# ---------------------------------------------------------------------------

_test_app = FastAPI()


@_test_app.get("/protected", dependencies=[])
async def _protected():
    return {"ok": True}


# We apply the dependency dynamically inside each test via patching,
# so we use a separate mini-app per scenario.

def _make_app_with_auth():
    from fastapi import Depends
    app = FastAPI()

    @app.get("/protected")
    async def protected(auth=Depends(require_api_key)):
        return {"ok": True}

    return app


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAuthAlwaysEnforced:
    """Authentication is always enforced — no bypass exists for empty keys."""

    def setup_method(self):
        self.app = _make_app_with_auth()
        self.client = TestClient(self.app)

    def test_no_header_returns_401_even_when_key_is_empty(self):
        with patch.object(settings, "BOT_API_KEY", ""):
            resp = self.client.get("/protected")
        assert resp.status_code == 401

    def test_wrong_value_returns_401_even_when_configured_key_is_empty(self):
        with patch.object(settings, "BOT_API_KEY", ""):
            resp = self.client.get("/protected", headers={"X-API-Key": "anything"})
        assert resp.status_code == 401


class TestAuthEnabled:
    """When BOT_API_KEY is set, only the correct key is accepted."""

    KEY = "super-secret-key"

    def setup_method(self):
        self.app = _make_app_with_auth()
        self.client = TestClient(self.app)

    def test_correct_key_returns_200(self):
        with patch.object(settings, "BOT_API_KEY", self.KEY):
            resp = self.client.get("/protected", headers={"X-API-Key": self.KEY})
        assert resp.status_code == 200

    def test_wrong_key_returns_401(self):
        with patch.object(settings, "BOT_API_KEY", self.KEY):
            resp = self.client.get("/protected", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_missing_header_returns_401(self):
        with patch.object(settings, "BOT_API_KEY", self.KEY):
            resp = self.client.get("/protected")
        assert resp.status_code == 401

    def test_empty_header_returns_401(self):
        with patch.object(settings, "BOT_API_KEY", self.KEY):
            resp = self.client.get("/protected", headers={"X-API-Key": ""})
        assert resp.status_code == 401
