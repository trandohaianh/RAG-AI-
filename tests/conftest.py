"""Dùng chung cho mọi test: bộ đếm rate-limit đăng nhập không được rò giữa các test."""

import pytest


@pytest.fixture(autouse=True)
def _reset_auth_rate_limits():
    from auth import ratelimit

    ratelimit.reset()
    yield
    ratelimit.reset()
