"""Celery discovery imports both CPU and LLM tasks; run separate queue workers."""
from .runtime import celery_app
from . import service  # noqa: F401 registers edudss_engine.train

celery_app.conf.task_routes = {
    "edudss_engine.train": {"queue": "cpu"},
    "edudss_engine.generate": {"queue": "llm"},
}
