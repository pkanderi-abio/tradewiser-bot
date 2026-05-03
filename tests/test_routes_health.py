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
