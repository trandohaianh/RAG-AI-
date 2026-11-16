"""Synthetic retrieval evidence, truthful run accounting, and lexical diagnostics."""
import json
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from rag.bm25 import BM25_AVAILABLE
from rag.grounding import sentence_grounding_score
from scripts import run_eval_benchmark as benchmark


@pytest.mark.skipif(not BM25_AVAILABLE, reason="rank_bm25 unavailable")
def test_fieldops_fixture_evidence_recall_and_no_evidence_behavior():
    report = benchmark.run_offline()
    assert report["planned"] == report["completed"] == 16
    assert report["failed"] == report["skipped"] == 0
    assert report["all_expectations_passed"]
    assert report["metrics"]["evidence_recall_at_k"] == 1
    assert report["metrics"]["mrr_at_k"] >= 0.9
    assert report["metrics"]["no_evidence_detection_accuracy"] == 1
    assert report["scope"] == "synthetic BM25-only retrieval"
    assert report["generation"]["status"] == "not_evaluated"
    assert report["generation"]["refusal_rate"] is None
    assert report["model"] is None


def test_failed_case_is_reported_and_counts_zero_in_planned_denominator():
    def failing_search(query, k, source):
        if query == "E-204":
            raise OSError("simulated retrieval outage")
        return []
    report = benchmark.run_offline(search=failing_search)
    assert report["planned"] == 16
    assert report["completed"] == 15
    assert report["failed"] == 1
    assert report["status"] == "failed"
    assert report["results"][0]["error_type"] == "OSError"
    assert not report["all_expectations_passed"]
    assert report["metrics"]["evidence_recall_at_k"] == 0


def test_completed_but_incorrect_retrieval_is_not_presented_as_passed():
    report = benchmark.run_offline(search=lambda *args: [])
    assert report["status"] == "completed"
    assert report["failed"] == 0
    assert not report["all_expectations_passed"]
    assert report["metrics"]["evidence_recall_at_k"] == 0


def test_each_run_is_fresh_and_ignores_legacy_partial_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "eval_results.partial.json").write_text('[{"question": "old education dataset"}]', encoding="utf-8")
    first = benchmark.run_offline(search=lambda *args: [])
    second = benchmark.run_offline(search=lambda *args: [])
    assert first["run_id"] != second["run_id"]
    assert first["dataset_sha256"] == second["dataset_sha256"]
    assert first["implementation_sha256"] == second["implementation_sha256"]
    assert len(first["results"]) == len(second["results"]) == 16


@pytest.mark.parametrize("mutation", ["duplicate_id", "unknown_evidence", "missing_expectation"])
def test_invalid_dataset_is_rejected_before_evaluation(tmp_path, mutation):
    data = benchmark.load_dataset(benchmark.DEFAULT_DATASET)
    if mutation == "duplicate_id":
        data["documents"][1]["id"] = data["documents"][0]["id"]
    elif mutation == "unknown_evidence":
        data["cases"][0]["expected_evidence"] = ["absent-evidence"]
    else:
        data["cases"][0]["expected_evidence"] = []
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        benchmark.run_offline(path, search=lambda *args: [])


def test_cli_gate_returns_failure_for_retrieval_regression(tmp_path, monkeypatch):
    original = benchmark.run_offline
    monkeypatch.setattr(benchmark, "run_offline", lambda path, k: original(path, k, search=lambda *args: []))
    output = tmp_path / "report.json"
    assert benchmark.main(["--offline", "--output", str(output), "--fail-on-regression"]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["planned"] == 16
    assert not report["all_expectations_passed"]


@pytest.mark.parametrize("answer,context", [
    ("Được đóng điện.", "Không được đóng điện."),
    ("Không được đóng điện.", "Được đóng điện."),
    ("Cần đóng điện.", "Chưa đóng điện."),
    ("Close the power switch.", "Do not close the power switch."),
])
def test_basic_polarity_reversal_cannot_score_as_supported(answer, context):
    score = sentence_grounding_score(answer, context)
    assert score["ratio"] == 0
    assert score["per_sentence"][0]["polarity_mismatch"]
    assert score["semantic_accuracy"] is None


def test_supported_negation_is_preserved():
    score = sentence_grounding_score("Không được đóng điện.", "Không được đóng điện.")
    assert score["ratio"] == 1
    assert not score["per_sentence"][0]["polarity_mismatch"]


def test_unrelated_negative_sentence_does_not_reverse_positive_source():
    score = sentence_grounding_score("Kiểm tra dây nguồn.", "Kiểm tra dây nguồn. Không đóng điện khi chưa xác nhận.")
    assert score["ratio"] == 1
    assert not score["per_sentence"][0]["polarity_mismatch"]


@pytest.mark.parametrize("vector_failed,lexical_failed", [(True, False), (False, True)])
def test_hybrid_serves_surviving_retrieval_branch(monkeypatch, vector_failed, lexical_failed):
    from rag import retriever
    row = ("evidence", "manual.pdf", 1)
    def result(failed):
        if failed:
            raise ConnectionError("simulated outage")
        return [row]
    monkeypatch.setattr(retriever, "_search_sync", lambda *args: result(vector_failed))
    monkeypatch.setattr(retriever, "_bm25_search_sync", lambda *args: result(lexical_failed))
    assert retriever._hybrid_search_sync(1, "query", 3) == [row]


def test_hybrid_does_not_disguise_total_outage_as_no_evidence(monkeypatch):
    from rag import retriever
    def fail(*args):
        raise ConnectionError("unavailable")
    monkeypatch.setattr(retriever, "_search_sync", fail)
    monkeypatch.setattr(retriever, "_bm25_search_sync", fail)
    with pytest.raises(RuntimeError, match="Both retrieval branches failed"):
        retriever._hybrid_search_sync(1, "query", 3)


def test_recovery_failure_cannot_be_hidden_by_hybrid_fallback(monkeypatch):
    from rag import retriever
    def fail(*args):
        raise retriever.DocumentRecoveryError("unrecovered document")
    monkeypatch.setattr(retriever, "_search_sync", fail)
    monkeypatch.setattr(retriever, "_bm25_search_sync", lambda *args: [("stale", "manual.pdf", 1)])
    with pytest.raises(retriever.DocumentRecoveryError):
        retriever._hybrid_search_sync(1, "query", 3)


def test_snapshot_checks_recovery_before_returning_cached_chunks(monkeypatch):
    from rag import retriever
    @contextmanager
    def failed_recovery(*args, **kwargs):
        raise retriever.DocumentRecoveryError("unrecoverable")
        yield
    monkeypatch.setattr(retriever, "collection_read_lock", failed_recovery)
    monkeypatch.setattr(retriever, "get_cached_chunks", lambda *args: pytest.fail("stale cache read before recovery"))
    with pytest.raises(retriever.DocumentRecoveryError):
        retriever._read_snapshot(1, "query", 3, None, None, final_k=3)


def test_cache_publication_checks_recovery_before_writing(monkeypatch):
    from rag import retriever
    @contextmanager
    def failed_recovery(*args, **kwargs):
        raise retriever.DocumentRecoveryError("unrecoverable")
        yield
    monkeypatch.setattr(retriever, "collection_read_lock", failed_recovery)
    monkeypatch.setattr(retriever, "set_cached_chunks", lambda *args: pytest.fail("stale cache write before recovery"))
    with pytest.raises(retriever.DocumentRecoveryError):
        retriever._cache_snapshot(1, "query", 3, [], None, None, "old")


def test_default_benchmark_counts_real_bm25_failure_as_failed_case(monkeypatch):
    from rag import bm25
    def fail(*args, **kwargs):
        raise OSError("index unreadable")
    monkeypatch.setattr(bm25, "_get_index", fail)
    report = benchmark.run_offline()
    assert report["planned"] == report["failed"] == 16
    assert report["completed"] == 0
    assert report["status"] == "failed"
    assert report["metrics"]["no_evidence_detection_accuracy"] == 0


def test_malformed_search_result_cannot_contribute_metrics_after_case_failure():
    dataset = benchmark.load_dataset(benchmark.DEFAULT_DATASET)
    evidence = dataset["cases"][0]["expected_evidence"][0]
    document = next(doc for doc in dataset["documents"] if doc["id"] == evidence)
    row = (document["text"], document["source"], document["page"])

    def malformed(query, k, source):
        if query == dataset["cases"][0]["query"]:
            return [row, ("incomplete-row",)]
        return []

    report = benchmark.run_offline(search=malformed)
    assert report["failed"] == 1
    assert report["results"][0]["status"] == "failed"
    assert report["metrics"]["evidence_recall_at_k"] == 0
    assert report["metrics"]["mrr_at_k"] == 0
