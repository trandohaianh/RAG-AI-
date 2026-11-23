"""Bước 8 (đo tải 2026-11-23): dependency xác thực có truy vấn CSDL đồng bộ không được là `async`.

Nếu là `async`, truy vấn chạy trên event loop. Khi pool kết nối đã hết, event loop đứng chờ một
kết nối mà các request khác chỉ trả lại được qua chính event loop, nên mọi request treo tới
pool_timeout (30 giây). Với 50 người dùng cùng lúc, mọi yêu cầu đều mất đúng 30 giây.
"""

from __future__ import annotations

import inspect
import threading

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

import dependencies
from database import get_db


def test_auth_dependency_with_db_query_runs_in_threadpool():
    assert not inspect.iscoroutinefunction(dependencies.get_current_user_allow_unverified)


def test_many_parallel_requests_with_a_tiny_pool_do_not_deadlock(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'x.db'}",
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    sessions = sessionmaker(bind=engine)

    def db():
        session = sessions()
        try:
            yield session
        finally:
            session.close()

    def fake_user(session=Depends(get_db)):  # cùng kiểu với dependency thật: hàm thường + truy vấn
        return session.execute(text("SELECT 1")).scalar()

    app = FastAPI()

    @app.get("/x")
    def endpoint(user=Depends(fake_user), session=Depends(get_db)):
        return {"ok": session.execute(text("SELECT 2")).scalar() + user}

    app.dependency_overrides[get_db] = db
    results: list[int] = []
    with TestClient(app) as client:
        threads = [threading.Thread(target=lambda: results.append(client.get("/x").status_code)) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    assert results == [200] * 20
    engine.dispose()
