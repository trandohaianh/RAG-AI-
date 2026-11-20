"""Application upgrades preserve identities and serialize real SQLite startups."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timezone
from threading import Barrier

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import database
import schema_migrations as migrations
from auth.models import User
from chat import models as chat_models  # noqa: F401
from database import Base
from field import models as field_models  # noqa: F401
from saas.models import EmbedGuestSession


def create_legacy_schema(engine):
    """An old backup with a paid user and historical hard-delete orphan rows."""
    identity = "SERIAL PRIMARY KEY" if engine.dialect.name == "postgresql" else "INTEGER PRIMARY KEY"
    timestamp = "TIMESTAMP WITH TIME ZONE" if engine.dialect.name == "postgresql" else "DATETIME"
    with engine.begin() as conn:
        conn.execute(
            text(
                f"CREATE TABLE users (id {identity}, email VARCHAR(255) NOT NULL UNIQUE, "
                "hashed_password VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL, "
                f"subscription_plan VARCHAR(20), created_at {timestamp} DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users (id,email,hashed_password,role,subscription_plan) "
                "VALUES (1,'legacy@example.com','preserved-hash','admin','pro')"
            )
        )
        conn.execute(
            text(
                f"CREATE TABLE conversations (id {identity}, user_id INTEGER NOT NULL, "
                f"title VARCHAR(255) NOT NULL, created_at {timestamp} DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
        )
        conn.execute(
            text(
                f"CREATE TABLE messages (id {identity}, conversation_id INTEGER NOT NULL, role VARCHAR(20) NOT NULL, "
                f"content TEXT NOT NULL, sources_json TEXT, created_at {timestamp} DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
        )
        conn.execute(text("INSERT INTO conversations (id,user_id,title) VALUES (9,99,'Retain this audit')"))
        conn.execute(
            text("INSERT INTO messages (id,conversation_id,role,content) VALUES (10,9,'assistant','Historic answer')")
        )
        # These old backup tables deliberately lack FK constraints, as older SQLite installs did.
        conn.execute(
            text(
                f"CREATE TABLE api_keys (id {identity}, user_id INTEGER NOT NULL, name VARCHAR(120) NOT NULL, "
                "key_prefix VARCHAR(16) NOT NULL, key_hash VARCHAR(64) NOT NULL UNIQUE, "
                f"created_at {timestamp} DEFAULT CURRENT_TIMESTAMP NOT NULL, last_used_at {timestamp})"
            )
        )
        conn.execute(
            text("INSERT INTO api_keys (id,user_id,name,key_prefix,key_hash) VALUES (3,99,'old','old','old-key')")
        )
        conn.execute(
            text(
                f"CREATE TABLE embed_tokens (id {identity}, user_id INTEGER NOT NULL, label VARCHAR(120) NOT NULL, "
                "token_hash VARCHAR(64) NOT NULL UNIQUE, allowed_origins TEXT, active BOOLEAN NOT NULL, "
                f"created_at {timestamp} DEFAULT CURRENT_TIMESTAMP NOT NULL)"
            )
        )
        conn.execute(
            text("INSERT INTO embed_tokens (id,user_id,label,token_hash,active) VALUES (4,99,'old','old-embed',TRUE)")
        )
        EmbedGuestSession.__table__.create(conn)
        conn.execute(
            EmbedGuestSession.__table__.insert().values(
                id=5,
                token_hash="old-guest",
                embed_token_id=4,
                conversation_id=9,
                expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            )
        )


def assert_upgraded_legacy(engine):
    with Session(engine) as db:
        user = db.get(User, 1)
        assert (user.email, user.hashed_password, user.role, user.subscription_plan) == (
            "legacy@example.com",
            "preserved-hash",
            "admin",
            "pro",
        )
        assert user.token_version == 0 and user.billing_sync_version == 0
        assert user.deleted_at is None
        deleted = db.get(User, 99)
        assert deleted.deleted_at is not None and deleted.token_version == 1
        assert deleted.hashed_password == "!deleted"
        assert deleted.email.endswith("@deleted.invalid")
        assert db.execute(text("SELECT title FROM conversations WHERE id=9")).scalar_one() == "Retain this audit"
        assert db.execute(text("SELECT content FROM messages WHERE id=10")).scalar_one() == "Historic answer"
        assert db.execute(text("SELECT rating FROM messages WHERE id=10")).scalar_one() is None
        assert db.execute(text("SELECT COUNT(*) FROM api_keys")).scalar_one() == 0
        assert db.execute(text("SELECT active FROM embed_tokens WHERE id=4")).scalar_one() in (False, 0)
        assert db.execute(text("SELECT COUNT(*) FROM embed_guest_sessions")).scalar_one() == 0
        db.add(User(email="new@example.com", hashed_password="new-hash"))
        db.flush()
        new_user = db.query(User).filter_by(email="new@example.com").one()
        assert new_user.id > 99
        assert new_user.token_version == 0
        db.commit()


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'schema.db').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    yield engine
    engine.dispose()


def test_existing_backup_preserves_paid_user_and_repairs_deleted_identity(sqlite_engine):
    create_legacy_schema(sqlite_engine)
    assert migrations.run_migrations(sqlite_engine, Base.metadata) == (1, 2, 3, 4, 5, 6, 7)
    assert migrations.run_migrations(sqlite_engine, Base.metadata) == ()
    assert_upgraded_legacy(sqlite_engine)


def test_existing_revocations_are_preserved_and_null_defaults_repaired(sqlite_engine):
    create_legacy_schema(sqlite_engine)
    with sqlite_engine.begin() as conn:
        conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER"))
        conn.execute(text("ALTER TABLE users ADD COLUMN billing_sync_version INTEGER"))
        conn.execute(text("UPDATE users SET token_version=8, billing_sync_version=91 WHERE id=1"))
        conn.execute(
            text(
                "INSERT INTO users (id,email,hashed_password,role,subscription_plan) "
                "VALUES (2,'null@example.com','hash','user',NULL)"
            )
        )
    migrations.run_migrations(sqlite_engine, Base.metadata)
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT token_version,billing_sync_version FROM users WHERE id=1")).one() == (8, 91)
        assert conn.execute(
            text("SELECT subscription_plan,token_version,billing_sync_version FROM users WHERE id=2")
        ).one() == ("free", 0, 0)
        assert conn.execute(text("SELECT billing_sync_version FROM users WHERE id=99")).scalar_one() == 0


@pytest.mark.parametrize("legacy", [False, True])
def test_concurrent_startups_share_one_transactional_ledger(sqlite_engine, legacy):
    if legacy:
        create_legacy_schema(sqlite_engine)
    barrier = Barrier(4)

    def startup(_):
        barrier.wait()
        return migrations.run_migrations(sqlite_engine, Base.metadata)

    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(startup, range(4)))
    assert results.count((1, 2, 3, 4, 5, 6, 7)) == 1
    assert results.count(()) == 3
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one() == 7
        if legacy:
            assert conn.execute(text("SELECT COUNT(*) FROM users WHERE id=99")).scalar_one() == 1


def test_failed_migration_rolls_back_schema_data_and_version(sqlite_engine, monkeypatch):
    create_legacy_schema(sqlite_engine)

    def fail_after_upgrades(conn):
        conn.execute(text("UPDATE users SET email='should-rollback@example.com' WHERE id=1"))
        raise RuntimeError("injected migration failure")

    monkeypatch.setattr(
        migrations, "_MIGRATIONS", (*migrations._MIGRATIONS, migrations.Migration(4, "injected", fail_after_upgrades))
    )
    with pytest.raises(RuntimeError, match="injected"):
        migrations.run_migrations(sqlite_engine, Base.metadata)
    assert "schema_migrations" not in inspect(sqlite_engine).get_table_names()
    assert "token_version" not in {c["name"] for c in inspect(sqlite_engine).get_columns("users")}
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT email FROM users WHERE id=1")).scalar_one() == "legacy@example.com"
        assert conn.execute(text("SELECT COUNT(*) FROM api_keys")).scalar_one() == 1


def test_unhandled_missing_column_fails_instead_of_create_all_masking_it(sqlite_engine):
    with sqlite_engine.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
    with pytest.raises(migrations.SchemaMigrationError, match="users lacks"):
        migrations.run_migrations(sqlite_engine, Base.metadata)
    assert "schema_migrations" not in inspect(sqlite_engine).get_table_names()


def test_unknown_database_version_stops_startup(sqlite_engine):
    migrations.run_migrations(sqlite_engine, Base.metadata)
    with sqlite_engine.begin() as conn:
        conn.execute(text("INSERT INTO schema_migrations (version,name) VALUES (999,'future-release')"))
    with pytest.raises(migrations.SchemaMigrationError, match="Unknown or mismatched"):
        migrations.run_migrations(sqlite_engine, Base.metadata)


def test_init_db_uses_versioned_runner_before_quota_backfill(sqlite_engine, monkeypatch):
    create_legacy_schema(sqlite_engine)
    monkeypatch.setattr(database, "engine", sqlite_engine)
    database.init_db()
    database.init_db()
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one() == 7
        assert conn.execute(text("SELECT COUNT(*) FROM query_usage_migrations")).scalar_one() == 1


def test_version_two_database_upgrades_evidence_columns_without_rebuilding_history(sqlite_engine, monkeypatch):
    create_legacy_schema(sqlite_engine)
    with monkeypatch.context() as old:
        old.setattr(migrations, "_MIGRATIONS", migrations._MIGRATIONS[:2])
        assert migrations.run_migrations(sqlite_engine) == (1, 2)
    assert "evidence_json" not in {column["name"] for column in inspect(sqlite_engine).get_columns("messages")}
    assert migrations.run_migrations(sqlite_engine, Base.metadata) == (3, 4, 5, 6, 7)
    assert "evidence_json" in {column["name"] for column in inspect(sqlite_engine).get_columns("messages")}
    with sqlite_engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM messages WHERE evidence_json IS NOT NULL")).scalar_one() == 0
    assert migrations.run_migrations(sqlite_engine, Base.metadata) == ()
