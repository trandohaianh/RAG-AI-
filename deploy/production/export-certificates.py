"""Certbot deploy hook: export only the validated leaf/key pair, not account/CA keys."""
from pathlib import Path
import os
from cryptography import x509
from cryptography.hazmat.primitives import serialization
lineage=Path(os.environ["RENEWED_LINEAGE"])
if lineage != Path("/etc/letsencrypt/live/ai-systems"):
    raise SystemExit("Unexpected certificate lineage")
cert_bytes=(lineage/"fullchain.pem").read_bytes()
key_bytes=(lineage/"privkey.pem").read_bytes()
cert=x509.load_pem_x509_certificate(cert_bytes)
key=serialization.load_pem_private_key(key_bytes,password=None)
fmt=(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)
if cert.public_key().public_bytes(*fmt) != key.public_key().public_bytes(*fmt):
    raise SystemExit("Certificate and private key do not match")
for name,data in [("fullchain.pem",cert_bytes),("privkey.pem",key_bytes)]:
    target=Path("/exports")/name
    temporary=target.with_suffix(".new")
    with temporary.open("wb") as stream:
        stream.write(data);stream.flush();os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary,target)
