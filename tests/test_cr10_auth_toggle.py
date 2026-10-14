"""Comprehensive automated test suite for Zero-Friction Customer Handover & Auth Toggle (CR-10).

Verifies the dual-state execution model:
- When AUTH_ENABLED=false: immediate bypass injection, administrative permissions,
  uninhibited mutations, public config reflection, and zero-network client bootstrap.
- When AUTH_ENABLED=true: full Zero-Trust CR-7 perimeter enforcement, 401 on missing tokens.
"""

from __future__ import annotations

import os
from unittest.mock import patch
import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from contract_parser.app import create_app
from contract_parser.auth import (
    UserContext,
    UserRole,
    dispatch_email_sign_in_link,
    make_auth_dependency,
    require_admin,
)
from contract_parser.config import PipelineConfig


# ------------------------------------------------------------------------------
# Unit Tests (UT-AUTH-1 through UT-AUTH-4)
# ------------------------------------------------------------------------------

def test_ut_auth_1_config_parsing(monkeypatch: pytest.MonkeyPatch):
    """Verify AppConfig parses AUTH_ENABLED flexibly with whitespace stripping."""
    # Falsy variations
    for val in ("false", "FALSE", "0", "no", "No", " false ", " 0 "):
        monkeypatch.setenv("AUTH_ENABLED", val)
        cfg = PipelineConfig()
        assert cfg.auth_enabled is False, f"Failed for val: '{val}'"

    # Truthy variations
    for val in ("true", "TRUE", "1", "yes", "Yes", " true ", " 1 "):
        monkeypatch.setenv("AUTH_ENABLED", val)
        cfg = PipelineConfig()
        assert cfg.auth_enabled is True, f"Failed for val: '{val}'"


def test_ut_auth_2_bypass_injection():
    """Verify get_current_user returns deterministic Admin UserContext when auth_enabled is False."""
    import asyncio
    cfg = PipelineConfig(auth_enabled=False)
    get_user = make_auth_dependency(lambda: cfg)

    # Invoke without any bearer credentials
    mock_request = Request({"type": "http", "method": "GET", "path": "/api/v1/projects", "headers": []})
    user = asyncio.run(get_user(mock_request, bearer=None))

    assert isinstance(user, UserContext)
    assert user.email == "dev@invenergy.com"
    assert user.role == UserRole.ADMIN
    assert user.domain == "invenergy.com"
    assert user.uid == "dev_bypass_uid"
    assert user.is_admin is True


def test_ut_auth_3_admin_route_bypass():
    """Verify require_admin guard accepts the bypass context without raising HTTP 403."""
    bypass_user = UserContext(
        email="dev@invenergy.com",
        role=UserRole.ADMIN,
        domain="invenergy.com",
        uid="dev_bypass_uid",
    )
    result = require_admin(user=bypass_user)
    assert result == bypass_user


def test_ut_auth_4_production_enforcement():
    """Verify all protected endpoints strictly return HTTP 401 when AUTH_ENABLED=true without token."""
    cfg = PipelineConfig(auth_enabled=True, use_bigquery=False)
    app = create_app(config=cfg)
    client = TestClient(app)

    # Confidential endpoints must reject request without Authorization header
    endpoints = [
        "/api/v1/projects",
        "/api/v1/landowners",
        "/api/v1/portfolio/search",
        "/api/v1/documents",
        "/api/v1/auth/me",
    ]
    for endpoint in endpoints:
        res = client.get(endpoint)
        assert res.status_code == 401, f"Expected 401 for {endpoint}, got {res.status_code}"


# ------------------------------------------------------------------------------
# Integration Tests (IT-AUTH-1 through IT-AUTH-4)
# ------------------------------------------------------------------------------

def test_it_auth_1_public_config_endpoint():
    """Verify GET /api/v1/auth/config reflects auth_enabled state accurately."""
    # 1. When auth_enabled = False
    cfg_false = PipelineConfig(auth_enabled=False, use_bigquery=False)
    app_false = create_app(config=cfg_false)
    client_false = TestClient(app_false)

    res_false = client_false.get("/api/v1/auth/config")
    assert res_false.status_code == 200
    data_false = res_false.json()
    assert data_false["auth_enabled"] is False

    # 2. When auth_enabled = True
    cfg_true = PipelineConfig(auth_enabled=True, use_bigquery=False)
    app_true = create_app(config=cfg_true)
    client_true = TestClient(app_true)

    res_true = client_true.get("/api/v1/auth/config")
    assert res_true.status_code == 200
    data_true = res_true.json()
    assert data_true["auth_enabled"] is True


def test_it_auth_2_dispatch_link_in_bypass_mode():
    """Verify request-link endpoint informs caller that authentication is disabled."""
    cfg = PipelineConfig(auth_enabled=False, use_bigquery=False)
    app = create_app(config=cfg)
    client = TestClient(app)

    res = client.post(
        "/api/v1/auth/request-link",
        json={"email": "evaluator@invenergy.com"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "bypassed"
    assert "disabled" in data["message"].lower()


def test_it_auth_3_full_mutation_in_bypass_mode():
    """Verify administrative mutations succeed without any Authorization header when auth_enabled=False."""
    cfg = PipelineConfig(auth_enabled=False, use_bigquery=False)
    app = create_app(config=cfg)
    client = TestClient(app)

    # 1. GET /api/v1/auth/me returns admin profile
    me_res = client.get("/api/v1/auth/me")
    assert me_res.status_code == 200
    assert me_res.json()["role"] == "admin"
    assert me_res.json()["email"] == "dev@invenergy.com"

    # 2. POST /api/v1/projects (normally restricted by require_admin) succeeds
    create_res = client.post(
        "/api/v1/projects",
        json={
            "project_name": "Evaluation Solar Array Alpha",
            "energy_technology": "SOLAR",
            "erp_project_code": "ERP-EVAL-001",
        },
    )
    assert create_res.status_code == 200
    assert create_res.json()["project_name"] == "Evaluation Solar Array Alpha"

    # 3. List projects verifies persisted project
    list_res = client.get("/api/v1/projects")
    assert list_res.status_code == 200
    project_names = [p["project_name"] for p in list_res.json()["projects"]]
    assert "Evaluation Solar Array Alpha" in project_names


def test_it_auth_4_ui_html_bootstrap_contract():
    """Verify contract_parser/static/index.html includes the zero-network auth bypass contract."""
    html_path = os.path.join(os.path.dirname(__file__), "..", "contract_parser", "static", "index.html")
    assert os.path.exists(html_path)

    with open(html_path, "r", encoding="utf-8") as f:
        html_content = f.read()

    # Confirm critical elements of the bypass contract in initAuth()
    assert "!authConfig.auth_enabled" in html_content
    assert "dev@invenergy.com" in html_content
    assert "dev_bypass_uid" in html_content
    assert "applyUserRolePermissions" in html_content
    assert "loadPortfolioHierarchy" in html_content
