"""Hash và tạo secret cho API key / embed token."""
import hashlib
import secrets


def hash_secret(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Trả về (raw_key, key_hash, key_prefix)."""
    raw = f"rag_{secrets.token_urlsafe(32)}"
    return raw, hash_secret(raw), raw[:12]


def generate_embed_token() -> tuple[str, str]:
    """Trả về (raw_token, token_hash)."""
    raw = f"emb_{secrets.token_urlsafe(28)}"
    return raw, hash_secret(raw)
