"""Comprehensive automated test suite for Enterprise Passwordless Email Link Auth & Domain Gate (CR-7)."""

from __future__ import annotations

import time
from unittest.mock import patch
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from contract_parser.app import create_app
from contract_parser.auth import (
    ALLOWED_DOMAINS,
    SlidingWindowRateLimiter,
    UserContext,
    UserRole,
    is_allowed_domain,
)
from contract_parser.config import PipelineConfig


def test_allowed_domain_logic():
    """Verify that domain whitelisting accepts only @invenergy.com and @google.com."""
    assert is_allowed_domain("john.doe@invenergy.com") is True
    assert is_allowed_domain("Jane.Smith@Google.com") is True
    assert is_allowed_domain("contract_officer@invenergy.com") is True

    # Unauthorized domains
    assert is_allowed_domain("attacker@gmail.com") is False
    assert is_allowed_domain("executive@yahoo.com") is False
    assert is_allowed_domain("spoof@invenergy.com.attacker.com") is False
    assert is_allowed_domain("not_an_email") is False
    assert is_allowed_domain("") is False


def test_sliding_window_rate_limiter_ip():
    """Verify sliding-window rate limiter throttles IP after exceeding ip_limit."""
    limiter = SlidingWindowRateLimiter(ip_limit=3, ip_window_seconds=100, email_limit=10)
    client_ip = "198.51.100.1"

    limiter.check_and_record(client_ip, "user1@invenergy.com")
    limiter.check_and_record(client_ip, "user2@invenergy.com")
    limiter.check_and_record(client_ip, "user3@invenergy.com")

    # 4th request from same IP within window must raise 429
    with pytest.raises(HTTPException) as exc_info:
        limiter.check_and_record(client_ip, "user4@invenergy.com")
    assert exc_info.value.status_code == 429
    assert "network" in exc_info.value.detail.lower()


def test_sliding_window_rate_limiter_email():
    """Verify sliding-window rate limiter throttles repeated requests for same email address."""
    limiter = SlidingWindowRateLimiter(ip_limit=20, email_limit=2, email_window_seconds=100)
    target_email = "target@invenergy.com"

    limiter.check_and_record("198.51.100.1", target_email)
    limiter.check_and_record("198.51.100.2", target_email)

    # 3rd request for same email even from different IP must raise 429
    with pytest.raises(HTTPException) as exc_info:
        limiter.check_and_record("198.51.100.3", target_email)
    assert exc_info.value.status_code == 429
    assert "email" in exc_info.value.detail.lower()


def test_public_auth_config_endpoint():
    """Verify GET /api/v1/auth/config returns public configuration without requiring Bearer token."""
    cfg = PipelineConfig(
        auth_enabled=True,
        use_bigquery=False,
        firebase_api_key="mock_key",
        firebase_auth_domain="mock.firebaseapp.com",
    )
    app = create_app(config=cfg)
    client = TestClient(app)

    res = client.get("/api/v1/auth/config")
    assert res.status_code == 200
    data = res.json()
    assert data["auth_enabled"] is True
    assert data["firebase_api_key"] == "mock_key"
    assert data["firebase_auth_domain"] == "mock.firebaseapp.com"
    assert "google.com" in data["allowed_domains"]
    assert "invenergy.com" in data["allowed_domains"]


def test_request_sign_in_link_domain_enforcement():
    """Verify POST /api/v1/auth/request-link strictly gates requests to authorized domains."""
    cfg = PipelineConfig(auth_enabled=True, use_bigquery=False)
    app = create_app(config=cfg)
    client = TestClient(app)

    # 1. Unauthorized external domain -> HTTP 403 Forbidden
    rejected = client.post(
        "/api/v1/auth/request-link",
        json={"email": "attacker@gmail.com"},
    )
    assert rejected.status_code == 403
    assert "Access restricted" in rejected.json()["detail"]

    # 2. Authorized Invenergy domain -> HTTP 200 OK with simulated dispatch
    accepted = client.post(
        "/api/v1/auth/request-link",
        json={"email": "field_lead@invenergy.com"},
    )
    assert accepted.status_code == 200
    res_data = accepted.json()
    assert res_data["status"] == "sent"
    assert res_data["email"] == "field_lead@invenergy.com"


def test_unauthenticated_requests_rejected_when_auth_enabled():
    """Verify all protected portfolio and document endpoints reject unauthenticated requests with HTTP 401."""
    cfg = PipelineConfig(auth_enabled=True, use_bigquery=False)
    app = create_app(config=cfg)
    client = TestClient(app)

    # Protected endpoints
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/landowners").status_code == 401
    assert client.get("/api/v1/portfolio/search").status_code == 401
    assert client.get("/api/v1/documents").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_viewer_role_blocked_from_admin_mutations():
    """Verify viewer (@invenergy.com) can read portfolio data but is blocked (HTTP 403) from mutations."""
    cfg = PipelineConfig(auth_enabled=True, use_bigquery=False)
    app = create_app(config=cfg)

    # Mock token decoding to return Invenergy Viewer identity
    mock_decoded = {
        "email": "field_viewer@invenergy.com",
        "uid": "viewer_uid_123",
    }

    with patch("firebase_admin.auth.verify_id_token", return_value=mock_decoded):
        client = TestClient(app)
        auth_headers = {"Authorization": "Bearer mock_viewer_token"}

        # 1. Read /me -> confirms viewer role
        me_res = client.get("/api/v1/auth/me", headers=auth_headers)
        assert me_res.status_code == 200
        me_data = me_res.json()
        assert me_data["email"] == "field_viewer@invenergy.com"
        assert me_data["role"] == "viewer"
        assert me_data["domain"] == "invenergy.com"

        # 2. Read projects -> allowed
        proj_res = client.get("/api/v1/projects", headers=auth_headers)
        assert proj_res.status_code == 200

        # 3. Create project -> HTTP 403 Forbidden
        create_res = client.post(
            "/api/v1/projects",
            headers=auth_headers,
            json={"project_name": "Unauthorized Project", "energy_technology": "SOLAR"},
        )
        assert create_res.status_code == 403
        assert "Admin privileges are required" in create_res.json()["detail"]

        # 4. Upload document -> HTTP 403 Forbidden
        upload_res = client.post(
            "/api/v1/documents/upload",
            headers=auth_headers,
            files={"file": ("test.pdf", b"%PDF-1.4\n%mock", "application/pdf")},
        )
        assert upload_res.status_code == 403

        # 5. Review clause -> HTTP 403 Forbidden
        review_res = client.patch(
            "/api/v1/documents/doc_dummy/clauses/node_dummy",
            headers=auth_headers,
            json={"hitl_status": "APPROVED_BY_HUMAN"},
        )
        assert review_res.status_code == 403


def test_admin_role_allowed_for_mutations():
    """Verify admin (@google.com) is permitted to perform creation and administration actions."""
    cfg = PipelineConfig(auth_enabled=True, use_bigquery=False)
    app = create_app(config=cfg)

    # Mock token decoding to return Google Admin identity
    mock_decoded = {
        "email": "partner@google.com",
        "uid": "admin_uid_456",
    }

    with patch("firebase_admin.auth.verify_id_token", return_value=mock_decoded):
        client = TestClient(app)
        auth_headers = {"Authorization": "Bearer mock_admin_token"}

        # 1. Read /me -> confirms admin role
        me_res = client.get("/api/v1/auth/me", headers=auth_headers)
        assert me_res.status_code == 200
        assert me_res.json()["role"] == "admin"

        # 2. Create project -> allowed (HTTP 200)
        create_res = client.post(
            "/api/v1/projects",
            headers=auth_headers,
            json={
                "project_name": "Test Wind Farm Alpha",
                "energy_technology": "ONSHORE_WIND",
                "erp_project_code": "ERP-WND-TEST-01",
            },
        )
        assert create_res.status_code == 200
        assert create_res.json()["project_name"] == "Test Wind Farm Alpha"


def test_dev_bypass_mode():
    """Verify when AUTH_ENABLED=false, development bypass mode automatically injects Admin user context."""
    cfg = PipelineConfig(auth_enabled=False, use_bigquery=False)
    app = create_app(config=cfg)
    client = TestClient(app)

    # Without any Authorization header:
    me_res = client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["email"] == "dev@invenergy.com"
    assert me_res.json()["role"] == "admin"

    # Project listing succeeds without token
    proj_res = client.get("/api/v1/projects")
    assert proj_res.status_code == 200
