"""Production must not start with a development signing key."""

import pytest

import config


@pytest.mark.parametrize(
    "secret",
    [
        "",
        "short",
        "dev-secret-change-in-production-32ch",
        "your_jwt_secret_32chars_minimum",
    ],
)
def test_production_refuses_weak_or_placeholder_secret(monkeypatch, secret):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "SECRET_KEY", secret)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        config.validate_security_config()


def test_production_accepts_deployment_specific_key(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "SECRET_KEY", "test-only-unique-signing-key-of-32-or-more-characters")
    # Production cũng cần URL công khai https và CORS chính xác (tests/test_production_readiness.py).
    monkeypatch.setattr(config, "APP_PUBLIC_URL", "https://fieldops.example.com")
    monkeypatch.setattr(config, "API_PUBLIC_URL", "https://fieldops.example.com/api")
    monkeypatch.setenv("CORS_ORIGINS", "https://fieldops.example.com")
    config.validate_security_config()


def test_local_development_remains_available(monkeypatch):
    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "SECRET_KEY", "dev")
    config.validate_security_config()
