"""Tests platform security helpers."""
from saas.security import generate_api_key, generate_embed_token, hash_secret


def test_hash_secret_deterministic():
    assert hash_secret("abc") == hash_secret("abc")
    assert hash_secret("abc") != hash_secret("xyz")


def test_generate_api_key_unique():
    raw1, h1, p1 = generate_api_key()
    raw2, h2, p2 = generate_api_key()
    assert raw1 != raw2
    assert h1 != h2
    assert raw1.startswith("rag_")
    assert hash_secret(raw1) == h1


def test_generate_embed_token():
    raw, h = generate_embed_token()
    assert raw.startswith("emb_")
    assert hash_secret(raw) == h
