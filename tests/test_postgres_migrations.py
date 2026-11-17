"""Opt-in PostgreSQL 16 upgrades; each test owns a disposable UUID schema.

FIELDOPS_TEST_POSTGRES_URL must name a dedicated test database. The application's
DATABASE_URL/configured database is never selected by this fixture.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from threading import Barrier
import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from auth.models import User
from database import Base
import schema_migrations as migrations
from test_schema_migrations import create_legacy_schema, assert_upgraded_legacy


@pytest.fixture
def postgres_engine():
    url = os.environ.get("FIELDOPS_TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("Set FIELDOPS_TEST_POSTGRES_URL to a dedicated PostgreSQL test database")
    admin = create_engine(url)
    if admin.dialect.name != "postgresql":
        admin.dispose()
        pytest.fail("FIELDOPS_TEST_POSTGRES_URL must use PostgreSQL")
    schema = "fieldops_test_" + uuid.uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            # schema is generated above, never taken from config or user content.
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_postgres_legacy_upgrade_boolean_revocation_and_sequence(postgres_engine):
    create_legacy_schema(postgres_engine)
    assert migrations.run_migrations(postgres_engine, Base.metadata) == (1, 2, 3, 4, 5, 6, 7)
    assert migrations.run_migrations(postgres_engine, Base.metadata) == ()
    assert_upgraded_legacy(postgres_engine)
    columns = {c["name"]: c for c in inspect(postgres_engine).get_columns("users")}
    assert not columns["token_version"]["nullable"]
    assert not columns["billing_sync_version"]["nullable"]
    assert columns["deleted_at"]["type"].timezone is True


def test_postgres_fresh_schema_and_defaults(postgres_engine):
    migrations.run_migrations(postgres_engine, Base.metadata)
    with Session(postgres_engine) as db:
        user = User(email="fresh@example.com", hashed_password="hash")
        db.add(user)
        db.commit()
        assert user.token_version == 0 and user.billing_sync_version == 0
        assert user.deleted_at is None
    assert migrations.run_migrations(postgres_engine, Base.metadata) == ()


@pytest.mark.parametrize("legacy", [False, True])
def test_postgres_concurrent_startups(postgres_engine, legacy):
    if legacy:
        create_legacy_schema(postgres_engine)
    barrier = Barrier(4)

    def startup(_):
        barrier.wait()
        return migrations.run_migrations(postgres_engine, Base.metadata)

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(startup, range(4)))
    assert results.count((1, 2, 3, 4, 5, 6, 7)) == 1
    assert results.count(()) == 3
    with postgres_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one() == 7
        if legacy:
            assert conn.execute(text("SELECT COUNT(*) FROM users WHERE id=99")).scalar_one() == 1


def test_postgres_failed_upgrade_rolls_back_ddl_and_repairs(postgres_engine, monkeypatch):
    create_legacy_schema(postgres_engine)

    def fail(conn):
        conn.execute(text("UPDATE users SET email='should-rollback@example.com' WHERE id=1"))
        raise RuntimeError("injected PostgreSQL failure")

    monkeypatch.setattr(migrations, "_MIGRATIONS", (*migrations._MIGRATIONS, migrations.Migration(4, "injected", fail)))
    with pytest.raises(RuntimeError, match="injected PostgreSQL"):
        migrations.run_migrations(postgres_engine, Base.metadata)
    assert "schema_migrations" not in inspect(postgres_engine).get_table_names()
    assert "token_version" not in {c["name"] for c in inspect(postgres_engine).get_columns("users")}
    with postgres_engine.connect() as conn:
        assert conn.execute(text("SELECT email FROM users WHERE id=1")).scalar_one() == "legacy@example.com"
        assert conn.execute(text("SELECT COUNT(*) FROM api_keys")).scalar_one() == 1
    # Removing the injected failure must allow a clean retry of the entire upgrade.
    monkeypatch.setattr(migrations, "_MIGRATIONS", migrations._MIGRATIONS[:-1])
    assert migrations.run_migrations(postgres_engine, Base.metadata) == (1, 2, 3, 4, 5, 6, 7)
    assert_upgraded_legacy(postgres_engine)


def test_postgres_preserves_revocations_and_repairs_existing_null_defaults(postgres_engine):
    create_legacy_schema(postgres_engine)
    with postgres_engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER"))
        conn.execute(text("ALTER TABLE users ADD COLUMN billing_sync_version INTEGER"))
        conn.execute(text("UPDATE users SET token_version=8, billing_sync_version=91 WHERE id=1"))
        conn.execute(text(
            "INSERT INTO users (id,email,hashed_password,role,subscription_plan) "
            "VALUES (2,'null@example.com','hash','user',NULL)"
        ))
        conn.execute(text("SELECT setval(pg_get_serial_sequence('users','id'), 500, TRUE)"))
    migrations.run_migrations(postgres_engine, Base.metadata)
    with postgres_engine.begin() as conn:
        assert conn.execute(text("SELECT token_version,billing_sync_version FROM users WHERE id=1")).one() == (8, 91)
        assert conn.execute(text("SELECT subscription_plan,token_version,billing_sync_version FROM users WHERE id=2")).one() == ("free", 0, 0)
        assert conn.execute(text(
            "INSERT INTO users (email,hashed_password,role) VALUES ('after@example.com','hash','user') RETURNING id"
        )).scalar_one() > 500


def test_postgres_unknown_missing_model_column_fails_closed(postgres_engine):
    with postgres_engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id SERIAL PRIMARY KEY)"))
    with pytest.raises(migrations.SchemaMigrationError, match="users lacks"):
        migrations.run_migrations(postgres_engine, Base.metadata)
    assert "schema_migrations" not in inspect(postgres_engine).get_table_names()
