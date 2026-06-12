"""Tests for GET /health/ — should always be reachable without auth."""


def test_health_returns_200(client):
    resp = client.get("/health/")
    assert resp.status_code == 200


def test_health_returns_ok_status(client):
    data = client.get("/health/").json()
    assert data["status"] == "ok"


def test_health_no_api_key_required(authed_client):
    """Health endpoint must be accessible even when BOT_API_KEY is enforced."""
    c, _ = authed_client
    # No X-API-Key header supplied — should still succeed
    resp = c.get("/health/")
    assert resp.status_code == 200


def test_broker_health_returns_snapshot(client):
    """GET /health/broker returns the alpaca_client.broker_snapshot() payload.

    The endpoint is unauthenticated by design — it exposes no PII, only
    connection state, so an external uptime probe can hit it safely.
    """
    resp = client.get("/health/broker")
    assert resp.status_code == 200
    body = resp.json()
    # Required keys for the operator dashboard
    for key in (
        "status", "authenticated", "auth_failed_latched", "consecutive_failures",
        "last_error", "retry_cooldown_seconds", "base_url", "paper_mode",
    ):
        assert key in body, f"missing {key} in /health/broker response"
    assert body["status"] in ("ok", "degraded")


def test_broker_health_no_api_key_required(authed_client):
    c, _ = authed_client
    # /health/broker must be reachable without X-API-Key for uptime probes
    resp = c.get("/health/broker")
    assert resp.status_code == 200
