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
    paths=[root/"secrets",root/"tls",root/".env"]
    if any(p.exists() for p in paths):
        raise RuntimeError("Existing secrets/TLS/.env found: refusing to overwrite or rotate live credentials")
    (root/"secrets").mkdir(parents=True,mode=0o700)
    (root/"tls").mkdir(mode=0o700)
    cert_bytes=lambda c:c.public_bytes(serialization.Encoding.PEM)
    webkey,webca=make_ca("AI Systems Local Development CA")
    leafkey,leafcert=leaf(webkey,webca,"AI Systems Local Gateway",
        ["rag.localhost","api-rag.localhost","telecom.localhost","edu.localhost"])
    write_new(root/"tls/local-ca.crt",cert_bytes(webca))
    write_new(root/"tls/local-ca.key",key_bytes(webkey),0o600)
    write_new(root/"tls/fullchain.pem",cert_bytes(leafcert)+cert_bytes(webca))
    write_new(root/"tls/privkey.pem",key_bytes(leafkey),0o600)
    cakey,cacert=make_ca("AI Systems Kafka Private CA")
    write_new(root/"secrets/kafka_ca",cert_bytes(cacert))
    write_new(root/"secrets/kafka_ca_key",key_bytes(cakey),0o600)
    for name,cn,dns,server in [("broker","kafka-broker",["kafka"],True),
                              ("admin","kafka-admin",[],False),
                              ("client","telecom-analytics",[],False)]:
        key,cert=leaf(cakey,cacert,cn,dns,server=server,client=True)
        write_new(root/("secrets/kafka_"+name+"_key"),key_bytes(key))
        write_new(root/("secrets/kafka_"+name+"_cert"),cert_bytes(cert))
        write_new(root/("secrets/kafka_"+name+"_keystore"),key_bytes(key)+cert_bytes(cert))
    passwords={name:secrets.token_hex(32) for name in ["policy","analytics","rag","edu","postgres_admin","rag_db"]}
    for name,value in passwords.items():
        write_new(root/("secrets/"+name+"_password"),value)
    for name,host,db in [("policy","redis-policy",0),("analytics","redis-analytics",0),
                         ("rag","redis-rag",0),("edu","redis-edu",0)]:
        write_new(root/("secrets/"+name+"_redis_url"),f"redis://app:{passwords[name]}@{host}:6379/{db}")
    write_new(root/"secrets/edu_broker_url",f"redis://app:{passwords['edu']}@redis-edu:6379/1")
    write_new(root/"secrets/rag_database_url",f"postgresql+psycopg2://rag_app:{passwords['rag_db']}@postgres-rag:5432/rag")
    for name in ("rag_signing_key","edu_signing_key","telecom_api_key"):
        write_new(root/("secrets/"+name),secrets.token_hex(48))
    # Optional provider keys stay disabled until explicitly configured by the operator.
    props=("security.protocol=SSL\nssl.truststore.type=PEM\nssl.truststore.location=/run/secrets/kafka_ca\n"
           "ssl.keystore.type=PEM\nssl.keystore.location=/run/secrets/kafka_admin_keystore\n"
           "ssl.endpoint.identification.algorithm=https\n")
    write_new(root/"secrets/kafka_admin_properties",props)
    import json
    write_new(root/"secrets/kafka_client_json",json.dumps({"security.protocol":"SSL",
        "ssl.ca.location":"/run/secrets/kafka_ca", "ssl.certificate.location":"/run/secrets/kafka_client_cert",
        "ssl.key.location":"/run/secrets/kafka_client_key", "ssl.endpoint.identification.algorithm":"https"}))
    env=(root/".env.example").read_text(encoding="utf-8")
    env += "\nKAFKA_CLUSTER_ID="+base64.urlsafe_b64encode(uuid.uuid4().bytes).decode().rstrip("=")+"\n"
    write_new(root/".env",env,0o600)
    print("Generated local TLS and file secrets. No credentials printed. CA trust is a separate operator action.")


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory",type=Path,default=Path(__file__).resolve().parent)
    bootstrap(parser.parse_args().directory)
