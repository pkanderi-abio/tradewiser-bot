"""Tests for GET /quotes/* routes."""
import pytest
from unittest.mock import patch
from app.core.config import settings


# ---------------------------------------------------------------------------
# Watchlist endpoint
# ---------------------------------------------------------------------------

def test_list_watchlist_returns_200(client, mock_alpaca):
    resp = client.get("/quotes/")
    assert resp.status_code == 200


def test_list_watchlist_returns_list(client, mock_alpaca):
    data = client.get("/quotes/").json()
    assert "watchlist" in data
    assert isinstance(data["watchlist"], list)
    assert len(data["watchlist"]) > 0


# ---------------------------------------------------------------------------
# Single symbol quote
# ---------------------------------------------------------------------------

def test_get_quote_returns_200(client, mock_alpaca):
    resp = client.get("/quotes/AAPL")
    assert resp.status_code == 200


def test_get_quote_returns_symbol_and_quote(client, mock_alpaca):
    data = client.get("/quotes/AAPL").json()
    assert data["symbol"] == "AAPL"
    assert "quote" in data
    assert data["quote"]["pLast"] == 150.00


def test_get_quote_uppercases_symbol(client, mock_alpaca):
    resp = client.get("/quotes/aapl")
    assert resp.status_code == 200
    data = resp.json()
    assert data["symbol"] == "AAPL"
    mock_alpaca.get_quote.assert_called_with("AAPL")


def test_get_quote_returns_502_when_client_returns_none(client, mock_alpaca):
    mock_alpaca.get_quote.return_value = None
    resp = client.get("/quotes/UNKNOWN")
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Auth enforcement on quotes
# ---------------------------------------------------------------------------

def test_quotes_returns_401_when_key_set_and_missing(authed_client, mock_alpaca):
    c, _ = authed_client
    resp = c.get("/quotes/AAPL")  # no X-API-Key header
    assert resp.status_code == 401


def test_quotes_returns_200_with_correct_key(authed_client, mock_alpaca):
    c, key = authed_client
    resp = c.get("/quotes/AAPL", headers={"X-API-Key": key})
    assert resp.status_code == 200


def test_quotes_returns_401_with_wrong_key(authed_client, mock_alpaca):
    c, _ = authed_client
    resp = c.get("/quotes/AAPL", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 401
