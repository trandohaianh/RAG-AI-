"""Browser preflights must follow the configured environment and origin policy."""

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from cors import cors_options


def _preflight(environment, origin, environ=None):
    app = FastAPI()
    app.add_middleware(CORSMiddleware, **cors_options(environment, environ or {}))
    with TestClient(app) as client:
        return client.options(
            "/probe",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization,Content-Type",
            },
        )


@pytest.mark.parametrize("environment", ["production", "prod", "staging", "unknown"])
def test_deployments_require_explicit_origins(environment):
    for origin in ("http://localhost:3000", "http://192.168.1.7:3000", "https://[2001:db8::1]"):
        response = _preflight(environment, origin)
        assert response.status_code == 400
        assert "access-control-allow-origin" not in response.headers


def test_production_allows_only_configured_web_origin():
    options = {"CORS_ORIGINS": " https://field.example.com , https://admin.example.com "}
    response = _preflight("production", "https://field.example.com", options)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://field.example.com"
    assert response.headers["access-control-allow-credentials"] == "true"
    for origin in ("https://field.example.com.attacker.test", "http://192.168.1.7:3000"):
        assert _preflight("production", origin, options).status_code == 400


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:3000",
        "http://192.168.1.7:3000",
        "http://10.2.3.4:8081",
        "http://172.31.1.2:3000",
        "http://169.254.2.3:3000",
        "http://[::1]:3000",
        "http://[fd00::42]:8081",
        "http://[fe80::1]:8081",
    ],
)
def test_development_supports_local_devices(origin):
    response = _preflight("development", origin)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.example",
        "https://[2001:db8::1]",
        "http://172.32.1.1:3000",
    ],
)
def test_development_does_not_default_to_public_origins(origin):
    assert _preflight("development", origin).status_code == 400


def test_explicit_regex_is_respected_without_allowing_suffix_domains():
    options = {"CORS_ORIGIN_REGEX": r"https://[a-z]+\.field\.example"}
    assert _preflight("production", "https://pilot.field.example", options).status_code == 200
    assert _preflight("production", "https://pilot.field.example.attacker.test", options).status_code == 400


def test_development_lan_default_can_be_disabled_explicitly():
    response = _preflight(
        "development",
        "http://192.168.1.7:3000",
        {
            "CORS_ORIGINS": "",
            "CORS_ORIGIN_REGEX": "",
        },
    )
    assert response.status_code == 400
