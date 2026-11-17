"""Pipeline vận hành: KPI ngày, kiểm tra chất lượng dữ liệu, retention, backup, metric."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from auth.models import User
from chat.models import Conversation, Message
from costs.models import LlmUsageEvent
from database import Base, _enable_sqlite_foreign_keys
from field import models as field_models  # noqa: F401
from pipeline.jobs import models as pipeline_models  # noqa: F401
from pipeline.jobs.models import AnalyticsDaily, PipelineRun
from pipeline.jobs.runner import run_chain, run_job
from saas import models as saas_models  # noqa: F401


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    Base.metadata.create_all(engine)
    make = sessionmaker(bind=engine, autoflush=False)
    now = datetime.now(timezone.utc)
    with make() as db:
        db.add_all([User(id=1, email="a@example.com", hashed_password="x"),
                    User(id=2, email="b@example.com", hashed_password="x")])
        db.add_all([Conversation(id=1, user_id=1), Conversation(id=2, user_id=2)])
        db.add_all([
            Message(conversation_id=1, role="user", content="q1", created_at=now),
            Message(conversation_id=1, role="assistant", content="a1", rating=1, eval_faithfulness=0.9,
                    eval_relevancy=0.8, created_at=now),
            Message(conversation_id=2, role="user", content="q2", created_at=now),
            Message(conversation_id=2, role="assistant", content="a2", rating=-1, eval_faithfulness=0.5,
                    eval_relevancy=0.6, created_at=now),
        ])
        db.add_all([
            LlmUsageEvent(user_id=1, feature="chat", provider="gemini", model="gemini-2.5-flash",
                          input_tokens=1000, output_tokens=200, cost_usd=0.0008, latency_ms=900, created_at=now),
            LlmUsageEvent(user_id=2, feature="chat", provider="gemini", model="gemini-2.5-flash",
                          input_tokens=500, output_tokens=100, cost_usd=0.0004, latency_ms=1500,
                          status="error", created_at=now),
            LlmUsageEvent(user_id=1, feature="chat", provider="gemini", model="gemini-2.5-flash",
                          input_tokens=1, cost_usd=0.1, created_at=now - timedelta(days=500)),
        ])
        db.commit()
    yield make
    engine.dispose()


def test_daily_kpis_aggregate_raw_data_idempotently(factory):
    for _ in range(2):  # chạy lại không nhân đôi
        result = run_job("daily_kpis", session_factory=factory, backfill_days=1)
    assert result["status"] == "ok"
    with factory() as db:
        row = db.get(AnalyticsDaily, datetime.now(timezone.utc).date())
        assert db.query(AnalyticsDaily).count() == 1
    assert (row.active_users, row.questions, row.answers) == (2, 2, 2)
    assert (row.thumbs_up, row.thumbs_down) == (1, 1)
    assert row.judge_faithfulness_avg == pytest.approx(0.7)
    assert row.llm_cost_usd == pytest.approx(0.0012) and row.llm_errors == 1
    assert row.tokens_in == 1500 and row.llm_latency_p95_ms == 1470.0  # nội suy tuyến tính như báo cáo eval


def test_data_quality_flags_bad_rows(factory):
    assert run_job("data_quality", session_factory=factory)["status"] == "ok"
    with factory() as db:
        db.add(Message(conversation_id=1, role="assistant", content="x", rating=5))
        db.commit()
    result = run_job("data_quality", session_factory=factory)
    assert result["status"] == "fail"
    assert "rating_out_of_range" in result["details"]["failed"]


def test_retention_removes_only_expired_events(factory):
    result = run_job("retention", session_factory=factory, keep_days=400)
    assert result["rows"] == 1
    with factory() as db:
        assert db.query(LlmUsageEvent).count() == 2


def test_backup_rotates_sqlite_and_chroma(factory, tmp_path, monkeypatch):
    import config

    db_file = tmp_path / "app.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE t (x)")
    chroma = tmp_path / "chroma_db"
    chroma.mkdir()
    (chroma / "index.bin").write_bytes(b"vectors")
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{db_file}")
    monkeypatch.setattr(config, "CHROMA_DIR", chroma)
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    for _ in range(3):
        result = run_job("backup", session_factory=factory, keep=2)
        assert result["status"] == "ok" and result["rows"] == 2
    names = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert len([n for n in names if n.startswith("app-")]) == 2
    assert len([n for n in names if n.startswith("chroma-")]) == 2
    with sqlite3.connect(tmp_path / "backups" / [n for n in names if n.startswith("app-")][-1]) as conn:
        assert conn.execute("SELECT name FROM sqlite_master").fetchone() == ("t",)


def test_failed_job_is_recorded_not_raised(factory, monkeypatch):
    from pipeline.jobs import tasks

    def boom(db, **_):
        raise RuntimeError("warehouse down")

    monkeypatch.setitem(tasks.JOBS, "daily_kpis", boom)
    results = run_chain(["daily_kpis", "data_quality"], session_factory=factory)
    assert [r["status"] for r in results] == ["fail", "ok"]
    with factory() as db:
        runs = db.query(PipelineRun).order_by(PipelineRun.id).all()
    assert runs[0].status == "fail" and "warehouse down" in json.loads(runs[0].details_json)["message"]
    assert all(r.finished_at is not None for r in runs)
