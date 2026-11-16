"""Opt-in PostgreSQL: gold.daily_ingest_stats phải idempotent khi chạy lại cùng tệp.

FIELDOPS_TEST_POSTGRES_URL phải trỏ tới cơ sở dữ liệu test có quyền CREATE DATABASE. Mỗi test tạo
một database tạm (schema bronze/silver/gold có tên cố định nên không tách được bằng search_path) và xoá khi xong.
"""
import os
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def medallion_db(monkeypatch):
    url = os.environ.get("FIELDOPS_TEST_POSTGRES_URL", "")
    if not url:
        pytest.skip("Set FIELDOPS_TEST_POSTGRES_URL to a dedicated PostgreSQL test database")
    name = "fieldops_medallion_" + uuid.uuid4().hex[:12]
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(make_url(url).set(database=name))
    with engine.begin() as conn:
        conn.exec_driver_sql((ROOT / "infra" / "postgres" / "init.sql").read_text(encoding="utf-8"))
    import pipeline.db as pdb
    import pipeline.medallion as medallion

    monkeypatch.setattr(pdb, "_engine", engine)
    monkeypatch.setattr(pdb, "_SessionLocal", sessionmaker(autocommit=False, autoflush=False, bind=engine))
    monkeypatch.setattr(medallion, "is_medallion_enabled", lambda: True)
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


def _stats(engine):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT files_processed, chunks_created, validation_failures, users_active FROM gold.daily_ingest_stats"
        )).all()


def test_rerunning_same_file_does_not_double_count(medallion_db, tmp_path):
    from pipeline.medallion import run_medallion_for_file

    doc = tmp_path / "may_bien_ap.txt"
    doc.write_text("Bảo trì máy biến áp.\n\n" + " ".join(f"Mục {i}: kiểm tra mức dầu cách điện máy số {i}." for i in range(80)),
                   encoding="utf-8")
    first = run_medallion_for_file(7, doc, dag_run_id="run-1")
    after_first = _stats(medallion_db)
    run_medallion_for_file(7, doc, dag_run_id="run-1-retry")      # Airflow thử lại cùng tệp
    run_medallion_for_file(7, doc, dag_run_id="run-2")
    assert _stats(medallion_db) == after_first
    files, chunks, failures, users = after_first[0]
    assert (files, users, failures) == (1, 1, 0)
    assert chunks == first["silver_chunks"] > 0


def test_second_file_and_failed_validation_are_counted_once(medallion_db, tmp_path):
    from pipeline.medallion import run_medallion_for_file

    good = tmp_path / "a.txt"
    good.write_text(" ".join(f"Bước {i}: kiểm tra điện áp bình số {i} trước khi thay ắc quy trạm BTS." for i in range(60)),
                    encoding="utf-8")                              # các chunk khác nhau: đạt kiểm tra
    tiny = tmp_path / "b.txt"
    tiny.write_text("ngắn", encoding="utf-8")                     # chunk dưới 10 ký tự: không đạt kiểm tra
    for _ in range(2):
        run_medallion_for_file(7, good)
        run_medallion_for_file(8, tiny)
    files, _chunks, failures, users = _stats(medallion_db)[0]
    assert (files, failures, users) == (2, 1, 2)
