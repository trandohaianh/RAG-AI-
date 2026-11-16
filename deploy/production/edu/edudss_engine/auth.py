"""Signed service-issued identities. Enrollment is checked live by the API."""
from __future__ import annotations
import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import os
import jwt

ISSUER, AUDIENCE = "edudss-engine", "edudss-api"
ROLES = {"admin", "instructor", "student"}


def signing_key(value=None):
    key = value if value is not None else os.getenv("EDUDSS_ENGINE_SIGNING_KEY", "")
    if not isinstance(key, str) or len(key.encode()) < 32:
        raise ValueError("EDUDSS_ENGINE_SIGNING_KEY must contain at least 32 bytes.")
    return key


@dataclass(frozen=True)
class Principal:
    school_id: str
    principal_id: str
    role: str
    student_id: str | None = None

    def __post_init__(self):
        for value in (self.school_id, self.principal_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
                raise ValueError("Explicit school and principal identities are required.")
        if self.role not in ROLES:
            raise ValueError("Unsupported role.")
        if self.role == "student" and (not isinstance(self.student_id, str) or not self.student_id.strip() or len(self.student_id) > 128):
            raise ValueError("A student identity is required.")


def issue_token(principal: Principal, key=None, ttl_seconds=3600):
    if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 3600:
        raise ValueError("Token lifetime must be between 1 and 3600 seconds.")
    now = int(datetime.now(timezone.utc).timestamp())
    return jwt.encode(dict(iss=ISSUER, aud=AUDIENCE, sub=principal.principal_id,
        school=principal.school_id, role=principal.role, student_id=principal.student_id,
        iat=now, exp=now + ttl_seconds), signing_key(key), algorithm="HS256")


def verify_token(token, key=None):
    claims = jwt.decode(token, signing_key(key), algorithms=["HS256"],
        issuer=ISSUER, audience=AUDIENCE,
        options={"require": ["exp", "iat", "iss", "aud", "sub", "school", "role"]})
    return Principal(claims["school"], claims["sub"], claims["role"], claims.get("student_id"))


def main():
    parser = argparse.ArgumentParser(description="Trusted operator/service JWT issuer; never expose the signing key to browsers.")
    parser.add_argument("--school", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--role", choices=sorted(ROLES), required=True)
    parser.add_argument("--student-id")
    args = parser.parse_args()
    print(issue_token(Principal(args.school, args.user, args.role, args.student_id)))


if __name__ == "__main__":
    main()
