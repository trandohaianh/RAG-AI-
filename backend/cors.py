"""CORS defaults for local development and explicitly configured deployments."""
from collections.abc import Mapping


_LOCAL_ORIGINS = ",".join(
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in range(3000, 3010)
)
# Private IPv4, loopback/ULA/link-local IPv6. Public IPv6 is never a LAN default.
_LAN_ORIGIN = (
    r"https?://("
    r"10(?:\.\d{1,3}){3}"
    r"|192\.168(?:\.\d{1,3}){2}"
    r"|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r"|169\.254(?:\.\d{1,3}){2}"
    r"|\[(?:::1|[fF][cCdD][0-9a-fA-F]{2}:[0-9a-fA-F:]+|[fF][eE][89aAbB][0-9a-fA-F]:[0-9a-fA-F:]+)\]"
    r")(?::\d+)?"
)


def cors_options(app_env: str, environ: Mapping[str, str]) -> dict:
    from config import is_production

    local = not is_production(app_env)
    origins = environ.get("CORS_ORIGINS", _LOCAL_ORIGINS if local else "")
    regex = environ.get("CORS_ORIGIN_REGEX", _LAN_ORIGIN if local else "")
    return {
        "allow_origins": [origin.strip() for origin in origins.split(",") if origin.strip()],
        "allow_origin_regex": regex.strip() or None,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
