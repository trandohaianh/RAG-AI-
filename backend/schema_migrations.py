"""Transactional application-schema upgrades for SQLite and PostgreSQL.

The advisory/write lock covers inspection, DDL, repair, table creation and the
version ledger. Application processes must finish this before serving requests.
This module never reads configuration or opens the application's default DB.
"""
from dataclasses import dataclass
from typing import Callable
import secrets

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, func, inspect, select, text
from sqlalchemy.engine import Connection, Engine


class SchemaMigrationError(RuntimeError):
    """An unknown schema cannot safely serve the current application."""


_LEDGER = Table(
    "schema_migrations", MetaData(),
    Column("version", Integer, primary_key=True),
    Column("name", String(120), nullable=False),
    Column("applied_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)
# A stable application-specific bigint; transaction locks release on crash.
_POSTGRES_LOCK_KEY = 0x4649454C444F5053


def _upgrade_legacy_columns(conn: Connection) -> None:
    timestamp = "TIMESTAMP WITH TIME ZONE" if conn.dialect.name == "postgresql" else "DATETIME"
    additions = {
        "users": {
            "subscription_plan": "VARCHAR(20) NOT NULL DEFAULT 'free'",
            "stripe_customer_id": "VARCHAR(255)",
            "token_version": "INTEGER NOT NULL DEFAULT 0",
            "billing_sync_version": "INTEGER NOT NULL DEFAULT 0",
            "deleted_at": timestamp,
        },
        "messages": {
            "rating": "INTEGER", "eval_faithfulness": "FLOAT", "eval_relevancy": "FLOAT",
        },
    }
    tables = set(inspect(conn).get_table_names())
    for table_name, definitions in additions.items():
        if table_name not in tables:
            continue
        columns = {column["name"]: column for column in inspect(conn).get_columns(table_name)}
        for name, definition in definitions.items():
            if name not in columns:
                conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {definition}"))
        if table_name == "users":
            # Preserve existing paid plans and token revocations; only repair NULLs.
            for name, fallback in (("subscription_plan", "'free'"), ("token_version", "0"), ("billing_sync_version", "0")):
                conn.execute(text(f"UPDATE users SET {name} = {fallback} WHERE {name} IS NULL"))
                if conn.dialect.name == "postgresql":
                    conn.execute(text(f"ALTER TABLE users ALTER COLUMN {name} SET DEFAULT {fallback}"))
                    conn.execute(text(f"ALTER TABLE users ALTER COLUMN {name} SET NOT NULL"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS ix_users_deleted_at ON users (deleted_at)"))


def repair_legacy_accounts(conn: Connection, table_names: set[str] | None = None) -> None:
    """Reserve identities referenced by old hard deletes; retain their audit rows."""
    tables = set(inspect(conn).get_table_names()) if table_names is None else table_names
    if "users" not in tables:
        return
    if conn.dialect.name == "postgresql":
        # Also exclude old application workers inserting users during sequence repair.
        conn.execute(text("LOCK TABLE users IN SHARE ROW EXCLUSIVE MODE"))
    references = {
        "conversations": ("user_id",), "api_keys": ("user_id",),
        "embed_tokens": ("user_id",), "password_reset_tokens": ("user_id",),
        "daily_query_usage": ("user_id",), "field_workspaces": ("created_by",),
        "field_workspace_members": ("user_id",),
        "field_service_cases": ("created_by", "assigned_to"),
        "field_service_case_steps": ("created_by", "completed_by"),
        "field_service_case_events": ("actor_id",), "field_client_actions": ("actor_id",),
        "field_case_photos": ("uploaded_by",), "field_integrations": ("created_by", "default_assignee"),
    }
    for table_name, names in references.items():
        if table_name not in tables:
            continue
        columns = {column["name"] for column in inspect(conn).get_columns(table_name)}
        for column in names:
            if column not in columns:
                continue
            missing = conn.execute(text(
                f"SELECT DISTINCT r.{column} FROM {table_name} r "
                f"LEFT JOIN users u ON u.id = r.{column} "
                f"WHERE r.{column} IS NOT NULL AND u.id IS NULL"
            )).scalars().all()
            for user_id in missing:
                conn.execute(text(
                    "INSERT INTO users "
                    "(id, email, hashed_password, role, subscription_plan, token_version, billing_sync_version, deleted_at) "
                    "VALUES (:id, :email, '!deleted', 'user', 'free', 1, 0, CURRENT_TIMESTAMP)"
                ), {"id": user_id, "email": f"deleted-legacy-{user_id}-{secrets.token_hex(16)}@deleted.invalid"})
    for table_name in ("field_workspace_members", "api_keys", "password_reset_tokens"):
        if table_name in tables:
            conn.execute(text(
                f"DELETE FROM {table_name} WHERE user_id IN "
                "(SELECT id FROM users WHERE deleted_at IS NOT NULL)"
            ))
    if "embed_tokens" in tables:
        # SQL FALSE is valid for both PostgreSQL BOOLEAN and SQLite's bool affinity.
        conn.execute(text(
            "UPDATE embed_tokens SET active = FALSE WHERE user_id IN "
            "(SELECT id FROM users WHERE deleted_at IS NOT NULL)"
        ))
        if "embed_guest_sessions" in tables:
            conn.execute(text(
                "DELETE FROM embed_guest_sessions WHERE embed_token_id IN "
                "(SELECT id FROM embed_tokens WHERE active = FALSE)"
            ))
    if conn.dialect.name == "postgresql":
        sequence = conn.execute(text("SELECT pg_get_serial_sequence('users', 'id')")).scalar_one_or_none()
        maximum = conn.execute(text("SELECT MAX(id) FROM users")).scalar_one_or_none()
        if sequence is not None and maximum is not None:
            # Never rewind an already advanced sequence. setval is not transactional;
            # a failed migration can leave a harmless gap, never a reused identity.
            conn.execute(text(
                "SELECT setval(CAST(:sequence AS regclass), "
                "GREATEST(:maximum, COALESCE(pg_sequence_last_value(CAST(:sequence AS regclass)), :maximum)), TRUE)"
            ), {"sequence": sequence, "maximum": maximum})


def _add_evidence_snapshots(conn: Connection) -> None:
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    for name in ("messages", "field_service_case_events"):
        if name in tables and "evidence_json" not in {column['name'] for column in inspector.get_columns(name)}:
            conn.execute(text(f"ALTER TABLE {name} ADD COLUMN evidence_json TEXT"))


def _add_identity_providers(conn: Connection) -> None:
    """Google Sign-In + xác thực email. Tài khoản cũ coi như đã xác thực để không bị khoá."""
    inspector = inspect(conn)
    if "users" not in set(inspector.get_table_names()):
        return
    timestamp = "TIMESTAMP WITH TIME ZONE" if conn.dialect.name == "postgresql" else "DATETIME"
    columns = {column["name"] for column in inspector.get_columns("users")}
    if "auth_provider" not in columns:
        conn.execute(text("ALTER TABLE users ADD COLUMN auth_provider VARCHAR(20) NOT NULL DEFAULT 'password'"))
    if "google_sub" not in columns:
        conn.execute(text("ALTER TABLE users ADD COLUMN google_sub VARCHAR(255)"))
    if "email_verified_at" not in columns:
        conn.execute(text(f"ALTER TABLE users ADD COLUMN email_verified_at {timestamp}"))
        source = "COALESCE(created_at, CURRENT_TIMESTAMP)" if "created_at" in columns else "CURRENT_TIMESTAMP"
        active = " WHERE deleted_at IS NULL" if "deleted_at" in columns else ""
        conn.execute(text(f"UPDATE users SET email_verified_at = {source}{active}"))
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_google_sub ON users (google_sub)"))


def _add_dispatch_and_integration_columns(conn: Connection) -> None:
    """Điều phối tuyến (toạ độ, khung giờ, lịch) và tích hợp CRM/NMS (mã nguồn ngoài)."""
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    timestamp = "TIMESTAMP WITH TIME ZONE" if conn.dialect.name == "postgresql" else "DATETIME"
    real = "DOUBLE PRECISION" if conn.dialect.name == "postgresql" else "FLOAT"
    if "field_service_cases" in tables:
        columns = {column["name"] for column in inspector.get_columns("field_service_cases")}
        for name, ddl in (
            ("latitude", real), ("longitude", real),
            ("priority", "VARCHAR(10) NOT NULL DEFAULT 'normal'"),
            ("window_start", timestamp), ("window_end", timestamp),
            ("est_minutes", "INTEGER NOT NULL DEFAULT 60"),
            ("scheduled_start", timestamp), ("route_order", "INTEGER"),
            ("external_source", "VARCHAR(32)"), ("external_ref", "VARCHAR(128)"),
        ):
            if name not in columns:
                conn.execute(text(f"ALTER TABLE field_service_cases ADD COLUMN {name} {ddl}"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_field_case_external "
            "ON field_service_cases (workspace_id, external_source, external_ref)"
        ))
    if "field_workspace_members" in tables:
        columns = {column["name"] for column in inspector.get_columns("field_workspace_members")}
        for name, ddl in (
            ("base_latitude", real), ("base_longitude", real),
            ("shift_start", "VARCHAR(5) NOT NULL DEFAULT '08:00'"),
            ("shift_end", "VARCHAR(5) NOT NULL DEFAULT '17:00'"),
        ):
            if name not in columns:
                conn.execute(text(f"ALTER TABLE field_workspace_members ADD COLUMN {name} {ddl}"))


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[Connection], None]


_MIGRATIONS = (
    Migration(1, "legacy_identity_and_message_columns", _upgrade_legacy_columns),
    Migration(2, "reserve_historic_identities", repair_legacy_accounts),
    Migration(3, "immutable_rag_evidence_snapshots", _add_evidence_snapshots),
    Migration(4, "google_identity_and_email_verification", _add_identity_providers),
    Migration(5, "field_dispatch_and_integrations", _add_dispatch_and_integration_columns),
)


def _validate_model_columns(conn: Connection, metadata: MetaData) -> None:
    inspector = inspect(conn)
    tables = set(inspector.get_table_names())
    for table in metadata.sorted_tables:
        if table.name not in tables:
            raise SchemaMigrationError(f"Required application table is missing: {table.name}")
        actual = {column["name"] for column in inspector.get_columns(table.name)}
        missing = set(table.columns.keys()) - actual
        if missing:
            raise SchemaMigrationError(
                f"Unsupported legacy schema: {table.name} lacks {', '.join(sorted(missing))}; add an explicit migration"
            )


def run_migrations(engine: Engine, metadata: MetaData | None = None) -> tuple[int, ...]:
    """Upgrade a supplied engine atomically; return versions applied by this worker.

    metadata.create_all only creates missing tables. Known column migrations run
    first, and final validation rejects other missing model columns explicitly.
    """
    dialect = engine.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise SchemaMigrationError(f"Application migrations do not support dialect: {dialect}")
    with engine.connect() as conn:
        try:
            if dialect == "sqlite":
                conn.exec_driver_sql("PRAGMA busy_timeout=30000")
                # Explicit BEGIN makes SQLite DDL transactional too (pysqlite legacy mode).
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
                conn.execute(text("SET LOCAL lock_timeout = '30s'"))
                conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _POSTGRES_LOCK_KEY})
            _LEDGER.create(conn, checkfirst=True)
            applied = dict(conn.execute(select(_LEDGER.c.version, _LEDGER.c.name)).all())
            known = {migration.version: migration.name for migration in _MIGRATIONS}
            for version, name in applied.items():
                if known.get(version) != name:
                    raise SchemaMigrationError(f"Unknown or mismatched application migration: {version} ({name})")
            versions = []
            for migration in _MIGRATIONS:
                if migration.version not in applied:
                    migration.apply(conn)
                    conn.execute(_LEDGER.insert().values(version=migration.version, name=migration.name))
                    versions.append(migration.version)
            if metadata is not None:
                metadata.create_all(conn)
                _validate_model_columns(conn, metadata)
            conn.commit()
            return tuple(versions)
        except BaseException:
            conn.rollback()
            raise
