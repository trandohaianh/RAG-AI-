"""Generate local-only CA, leaf certificates and random file secrets; never overwrite.

This does not trust a CA in your OS/browser, register DNS, contact ACME or start services.
"""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import secrets
import uuid
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def write_new(path, value, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as f:
        f.write(value.encode() if isinstance(value, str) else value)
    os.chmod(path, mode)


def key_bytes(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def make_ca(name):
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    dn = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(dn).issuer_name(dn).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5))
            .not_valid_after(now+timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
            .add_extension(x509.KeyUsage(True,False,False,False,False,True,True,False,False), True)
            .sign(key,hashes.SHA256()))
    return key, cert


def leaf(ca_key, ca_cert, name, dns_names, *, server=True, client=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    now = datetime.now(timezone.utc)
    eku = ([ExtendedKeyUsageOID.SERVER_AUTH] if server else []) + ([ExtendedKeyUsageOID.CLIENT_AUTH] if client else [])
    builder = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,name)]))
               .issuer_name(ca_cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=365))
               .add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
               .add_extension(x509.ExtendedKeyUsage(eku),False)
               .add_extension(x509.KeyUsage(True,False,True,False,False,False,False,False,False),True))
    if dns_names:
        builder=builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(d) for d in dns_names]),False)
    return key, builder.sign(ca_key,hashes.SHA256())


def bootstrap(root):
    root=Path(root)
    if any((root/x).exists() for x in ("secrets","tls",".env")):
        raise RuntimeError("Existing credentials found; refusing to overwrite")
    (root/"secrets").mkdir(parents=True,mode=0o700)
    (root/"tls").mkdir(mode=0o700)
    pem=lambda c:c.public_bytes(serialization.Encoding.PEM)
    cakey,ca=make_ca("Telecom Edu Local CA")
    serverkey,server=leaf(cakey,ca,"Telecom Edu Gateway",["telecom.localhost","edu.localhost"])
    write_new(root/"tls/local-ca.crt",pem(ca))
    write_new(root/"tls/local-ca.key",key_bytes(cakey),0o600)
    write_new(root/"tls/fullchain.pem",pem(server)+pem(ca))
    write_new(root/"tls/privkey.pem",key_bytes(serverkey),0o600)
    clientkey,client=leaf(cakey,ca,"trusted-telecom-core",[],server=False,client=True)
    write_new(root/"secrets/telecom_client_key",key_bytes(clientkey),0o600)
    write_new(root/"secrets/telecom_client_cert",pem(client))
    for name,host in [("policy","redis-policy"),("edu","redis-cache")]:
        pw=secrets.token_hex(32)
        write_new(root/("secrets/"+name+"_password"),pw)
        write_new(root/("secrets/"+name+"_redis_url"),f"redis://app:{pw}@{host}:6379/0")
        if name=="edu":write_new(root/"secrets/edu_broker_url",f"redis://app:{pw}@{host}:6379/1")
    for name in ["telecom_api_key","edu_signing_key"]:
        write_new(root/("secrets/"+name),secrets.token_hex(48))
    write_new(root/".env",(root/".env.example").read_text(encoding="utf-8"),0o600)
    print("Generated local HTTPS/mTLS certificates and random secrets; no OS trust or deployment performed.")

if __name__=="__main__":
    parser=argparse.ArgumentParser(description="Initialize local certificates and secrets once")
    parser.add_argument("--directory",type=Path,default=Path(__file__).resolve().parent)
    bootstrap(parser.parse_args().directory)
