"""Truthful evaluation accounting and explicit live/provider/content opt-ins."""
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from scripts import run_rag_evaluation as evaluation


@pytest.fixture
def dataset():
    return {
        "schema_version": 1, "dataset_id": "reviewed-fixture", "kind": "user_curated",
        "target": {"user_id": 17, "workspace_id": 8, "collection": "workspace_8"},
        "cases": [
            {"id": "supported", "query": "private incident question", "source": "confidential.pdf",
             "expected_evidence": [{"source": "confidential.pdf", "page": 2}], "expect_no_evidence": False},
            {"id": "absent", "query": "private absent question", "expected_evidence": [], "expect_no_evidence": True},
        ],
    }


class FakePipeline:
    config = {"both_retrieval_branches_required": True, "cache": "bypassed"}

    def __init__(self, retrieve=None, generate=None):
        self.calls = []
        self.retrieve_fn = retrieve
        self.generate_fn = generate

    def retrieve(self, target, case, k):
        self.calls.append((target, case, k))
        if self.retrieve_fn:
            return self.retrieve_fn(target, case, k)
        rows = [("private source evidence", "confidential.pdf", 2)] if case["id"] == "supported" else []
        return evaluation.Snapshot(rows, {"collection_revision": "revision-1"})

    def generate(self, query, rows):
        if self.generate_fn:
            return self.generate_fn(query, rows)
        return "private generated answer with no refusal keyword", bool(rows)


def _save(tmp_path, data):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_template_validates_offline_and_is_never_a_real_measurement(monkeypatch):
    monkeypatch.setattr(evaluation, "LivePipeline", lambda *args: pytest.fail("offline validation touched providers"))
    data = evaluation.load_dataset(evaluation.DEFAULT_TEMPLATE)
    report = evaluation.evaluate(data)
    assert data["kind"] == "template"
    assert report["status"] == "validated"
    assert (report["planned"], report["completed"], report["failed"], report["skipped"]) == (2, 0, 0, 2)
    assert report["retrieval"]["evidence_recall_at_k"] is None
    assert report["retrieval"]["all_expectations_passed"] is None
    assert report["generation"]["refusal_rate"] is None
    assert evaluation.main([]) == 0
    with pytest.raises(ValueError, match="Template"):
        evaluation.evaluate(data, mode="retrieval", pipeline=FakePipeline())


@pytest.mark.parametrize("mutation", [
    "mismatch_collection", "bool_user", "negative_workspace", "duplicate_id", "no_expectation",
    "two_expectations", "invalid_page", "bool_page", "missing_page", "source_mismatch", "empty_skip",
])
def test_invalid_annotations_rejected_before_live_calls(tmp_path, dataset, mutation):
    if mutation == "mismatch_collection":
        dataset["target"]["collection"] = "user_17"
    elif mutation == "bool_user":
        dataset["target"]["user_id"] = True
    elif mutation == "negative_workspace":
        dataset["target"]["workspace_id"] = -1
    elif mutation == "duplicate_id":
        dataset["cases"][1]["id"] = dataset["cases"][0]["id"]
    elif mutation == "no_expectation":
        dataset["cases"][0]["expected_evidence"] = []
    elif mutation == "two_expectations":
        dataset["cases"][0]["expect_no_evidence"] = True
    elif mutation == "invalid_page":
        dataset["cases"][0]["expected_evidence"][0]["page"] = 0
    elif mutation == "bool_page":
        dataset["cases"][0]["expected_evidence"][0]["page"] = True
    elif mutation == "missing_page":
        del dataset["cases"][0]["expected_evidence"][0]["page"]
    elif mutation == "source_mismatch":
        dataset["cases"][0]["source"] = "different.pdf"
    else:
        dataset["cases"][0]["skip_reason"] = " "
    with pytest.raises(ValueError):
        evaluation.load_dataset(_save(tmp_path, dataset))


def test_real_evidence_metrics_and_scope_forwarding_without_sensitive_report(dataset):
    pipeline = FakePipeline()
    report = evaluation.evaluate(dataset, mode="retrieval", pipeline=pipeline, k=3)
    assert report["planned"] == report["completed"] == 2
    assert report["failed"] == report["skipped"] == 0
    assert report["retrieval"]["all_expectations_passed"]
    assert report["retrieval"]["evidence_recall_at_k"] == 1
    assert report["retrieval"]["mrr_at_k"] == 1
    assert report["retrieval"]["no_evidence_detection_accuracy"] == 1
    assert pipeline.calls[0][0] == dataset["target"]
    assert pipeline.calls[0][1]["source"] == "confidential.pdf"
    assert pipeline.calls[0][2] == 3
    serialized = json.dumps(report)
    for secret_content in ("private incident question", "private absent question", "confidential.pdf", "private source evidence"):
        assert secret_content not in serialized
    assert report["results"][0]["retrieved_evidence"][0]["page"] == 2


def test_outage_is_failed_and_cannot_be_successful_no_evidence(dataset):
    def retrieve(*args):
        raise ConnectionError("private question and provider key should not be recorded")
    report = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline(retrieve=retrieve))
    assert (report["completed"], report["failed"]) == (0, 2)
    assert report["retrieval"]["no_evidence_detection_accuracy"] == 0
    assert report["retrieval"]["evidence_recall_at_k"] == 0
    assert report["results"][1]["error_type"] == "ConnectionError"
    assert "empty_retrieval" not in report["results"][1]
    assert "provider key" not in json.dumps(report)


def test_malformed_hit_cannot_contribute_to_successful_evidence_metric(dataset):
    def retrieve(*args):
        return evaluation.Snapshot([("valid evidence", "confidential.pdf", 2), ("bad-row",)])
    report = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline(retrieve=retrieve))
    assert report["failed"] == 2
    assert report["retrieval"]["evidence_recall_at_k"] == 0
    assert report["retrieval"]["mrr_at_k"] == 0
    assert report["retrieval"]["no_evidence_detection_accuracy"] == 0


def test_source_page_matches_are_exact_and_null_is_not_any_page(dataset):
    dataset["cases"][0]["expected_evidence"][0]["page"] = None
    report = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline())
    assert report["completed"] == 2
    assert report["retrieval"]["evidence_recall_at_k"] == 0
    assert not report["retrieval"]["all_expectations_passed"]


def test_generation_requires_human_review_and_content_opt_in(dataset):
    private = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline(), generate=True)
    public = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline(), generate=True, include_content=True)
    assert private["generation"]["refusal_rate"] is None
    assert private["generation"]["correctness"] is None
    assert private["generation"]["safety"] is None
    assert "private generated answer" not in json.dumps(private)
    assert public["results"][0]["generation"]["answer"].startswith("private generated answer")
    assert public["results"][0]["retrieved_evidence"][0]["text"] == "private source evidence"
    assert public["results"][1]["generation"]["model_called"] is False
    assert public["results"][1]["generation"]["review"] == {
        "status": "required", "correctness": None, "safety": None, "refusal": None,
    }


def test_generation_failure_preserves_retrieval_metrics_but_fails_overall(dataset):
    def generate(*args):
        raise TimeoutError("secret content")
    report = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline(generate=generate), generate=True)
    assert report["completed"] == 0
    assert report["failed"] == 2
    assert report["retrieval"]["completed"] == 2
    assert report["retrieval"]["all_expectations_passed"]
    assert report["generation"]["failed"] == 2
    assert "secret content" not in json.dumps(report)


def test_annotation_skip_is_counted_and_excluded_from_metric_denominator(dataset):
    dataset["cases"][0]["skip_reason"] = "private reviewer comment"
    report = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline())
    assert (report["planned"], report["completed"], report["skipped"]) == (2, 1, 1)
    assert report["retrieval"]["supported_planned"] == 0
    assert report["retrieval"]["evidence_recall_at_k"] is None
    assert "private reviewer comment" not in json.dumps(report)


def test_runs_have_fingerprints_and_never_resume_old_reports(dataset):
    first = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline())
    second = evaluation.evaluate(dataset, mode="retrieval", pipeline=FakePipeline())
    assert first["run_id"] != second["run_id"]
    assert first["dataset_sha256"] == second["dataset_sha256"]
    assert first["implementation_sha256"] == second["implementation_sha256"]
    assert first["runtime"]["python"]
    changed = copy.deepcopy(dataset)
    changed["cases"][0]["query"] += " changed"
    third = evaluation.evaluate(changed)
    assert third["dataset_sha256"] != first["dataset_sha256"]


@pytest.mark.parametrize("args", [
    ["--mode", "retrieval"],
    ["--generate"],
    ["--mode", "retrieval", "--allow-external-calls"],
])
def test_cli_requires_explicit_provider_generation_and_report_choices(monkeypatch, args):
    monkeypatch.setattr(evaluation, "LivePipeline", lambda *args: pytest.fail("providers initialized before opt-in"))
    with pytest.raises(SystemExit) as exc:
        evaluation.main(args)
    assert exc.value.code == 2


def test_cli_writes_only_validation_report_offline(tmp_path, dataset, monkeypatch):
    monkeypatch.setattr(evaluation, "LivePipeline", lambda *args: pytest.fail("offline run initialized providers"))
    output = tmp_path / "validation.json"
    assert evaluation.main(["--dataset", str(_save(tmp_path, dataset)), "--output", str(output)]) == 0
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "validated"
    assert report["completed"] == 0
    assert report["skipped"] == 2


def test_report_cannot_overwrite_dataset_or_application_data(tmp_path, dataset):
    path = _save(tmp_path, dataset)
    report = evaluation.evaluate(dataset)
    with pytest.raises(ValueError):
        evaluation._write_report(path, report, path)
    with pytest.raises(ValueError):
        evaluation._write_report(tmp_path / "data" / "document.json", report, path, data_roots=(tmp_path / "data",))
    assert json.loads(path.read_text(encoding="utf-8"))["target"] == dataset["target"]


def test_live_snapshot_refuses_pending_recovery_without_search_or_repair(tmp_path, monkeypatch):
    import config
    import chromadb
    from rag import document_lifecycle as lifecycle
    from rag import retriever
    index_dir = tmp_path / "chroma"
    index_dir.mkdir()
    monkeypatch.setattr(config, "CHROMA_DIR", index_dir)
    pipeline = evaluation.LivePipeline(3)
    collection = "user_17"
    lifecycle._journal_path(index_dir, collection).write_text("corrupt journal", encoding="utf-8")
    monkeypatch.setattr(retriever, "_search_sync", lambda *args: pytest.fail("searched unrecovered index"))
    monkeypatch.setattr(chromadb, "PersistentClient", lambda *args, **kwargs: pytest.fail("opened unrecovered index"))
    monkeypatch.setattr(lifecycle, "recover_pending", lambda *args, **kwargs: pytest.fail("evaluation repaired real user data"))
    with pytest.raises(lifecycle.DocumentRecoveryError):
        pipeline.retrieve({"user_id": 17, "collection": collection}, {"query": "query"}, 3)


def test_live_snapshot_requires_existing_collection_before_provider_search(tmp_path, monkeypatch):
    import config
    import chromadb
    from rag import retriever
    index_dir = tmp_path / "chroma"
    index_dir.mkdir()
    monkeypatch.setattr(config, "CHROMA_DIR", index_dir)
    pipeline = evaluation.LivePipeline(3)
    class MissingClient:
        def get_collection(self, name):
            raise LookupError("absent collection")
    monkeypatch.setattr(chromadb, "PersistentClient", lambda **kwargs: MissingClient())
    monkeypatch.setattr(retriever, "_search_sync", lambda *args: pytest.fail("provider called for missing index"))
    with pytest.raises(LookupError):
        pipeline.retrieve({"user_id": 17, "collection": "user_17"}, {"query": "query"}, 3)


def test_live_snapshot_strict_branch_failure_is_not_hidden_by_other_evidence(tmp_path, monkeypatch):
    import config
    import chromadb
    from rag import retriever
    index_dir = tmp_path / "chroma"
    index_dir.mkdir()
    monkeypatch.setattr(config, "CHROMA_DIR", index_dir)
    pipeline = evaluation.LivePipeline(3)
    class ExistingClient:
        def get_collection(self, name):
            return object()
    monkeypatch.setattr(chromadb, "PersistentClient", lambda **kwargs: ExistingClient())
    monkeypatch.setattr(retriever, "_search_sync", lambda *args: [("evidence", "manual.pdf", 1)])
    def fail(*args):
        raise ConnectionError("BM25 broken")
    monkeypatch.setattr(retriever, "_bm25_search_sync", fail)
    with pytest.raises(ConnectionError):
        pipeline.retrieve({"user_id": 17, "collection": "user_17"}, {"query": "query"}, 3)


@pytest.mark.parametrize("destination", ["dataset", "data", "extension"])
def test_cli_preflights_output_before_initializing_provider(tmp_path, dataset, monkeypatch, destination):
    path = _save(tmp_path, dataset)
    outputs = {"dataset": path, "data": tmp_path / "data" / "report.json", "extension": tmp_path / "app.db"}
    monkeypatch.setattr(evaluation, "_application_data_roots", lambda: (tmp_path / "data",))
    monkeypatch.setattr(evaluation, "LivePipeline", lambda *args: pytest.fail("provider initialized before output preflight"))
    assert evaluation.main([
        "--dataset", str(path), "--mode", "retrieval", "--allow-external-calls", "--output", str(outputs[destination]),
    ]) == 2


def test_cli_regression_gate_rejects_zero_evaluated_cases(tmp_path, dataset, monkeypatch):
    for case in dataset["cases"]:
        case["skip_reason"] = "not reviewed yet"
    path = _save(tmp_path, dataset)
    output = tmp_path / "report.json"
    monkeypatch.setattr(evaluation, "_application_data_roots", lambda: ())
    monkeypatch.setattr(evaluation, "LivePipeline", lambda *args: FakePipeline())
    assert evaluation.main([
        "--dataset", str(path), "--mode", "retrieval", "--allow-external-calls", "--output", str(output),
        "--fail-on-regression",
    ]) == 1
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["completed"] == report["failed"] == 0
    assert report["skipped"] == 2
    assert report["retrieval"]["all_expectations_passed"] is None



def test_live_pipeline_uses_both_production_branches_and_reranks_their_fusion(tmp_path, monkeypatch):
    import config
    import chromadb
    from rag import retriever, rerank
    index_dir = tmp_path / "chroma"
    index_dir.mkdir()
    monkeypatch.setattr(config, "CHROMA_DIR", index_dir)
    monkeypatch.setattr(config, "RETRIEVE_CANDIDATES", 7)
    class Collection:
        def count(self):
            return 2
    class ExistingClient:
        def get_collection(self, name):
            assert name == "workspace_8"
            return Collection()
    monkeypatch.setattr(chromadb, "PersistentClient", lambda **kwargs: ExistingClient())
    calls = []
    vector_row = ("vector evidence", "manual.pdf", 1)
    lexical_row = ("technical E-204 evidence", "manual.pdf", 2)
    def search(branch, row):
        def invoke(*args):
            calls.append((branch, args))
            return [row]
        return invoke
    monkeypatch.setattr(retriever, "_search_sync", search("vector", vector_row))
    monkeypatch.setattr(retriever, "_bm25_search_sync", search("bm25", lexical_row))
    def rank(query, rows, top_n):
        assert query == "E-204"
        assert rows == [vector_row, lexical_row]
        assert top_n == 1
        return [lexical_row]
    monkeypatch.setattr(rerank, "rerank_chunks", rank)
    snapshot = evaluation.LivePipeline(1).retrieve(
        {"user_id": 17, "workspace_id": 8, "collection": "workspace_8"},
        {"query": "E-204", "source": "manual.pdf"}, 1,
    )
    assert calls == [
        ("vector", (17, "E-204", 7, "manual.pdf", 8)),
        ("bm25", (17, "E-204", 7, "manual.pdf", 8)),
    ]
    assert snapshot.rows == [lexical_row]
    assert snapshot.details == {
        "collection_revision": "legacy", "collection_chunks": 2,
        "vector_candidates": 1, "bm25_candidates": 1,
    }


def test_cli_retrieval_does_not_generate_without_opt_in(tmp_path, dataset, monkeypatch):
    path = _save(tmp_path, dataset)
    monkeypatch.setattr(evaluation, "_application_data_roots", lambda: ())
    def no_generation(*args):
        pytest.fail("retrieval-only evaluation generated an answer")
    monkeypatch.setattr(evaluation, "LivePipeline", lambda *args: FakePipeline(generate=no_generation))
    assert evaluation.main([
        "--dataset", str(path), "--mode", "retrieval", "--allow-external-calls",
        "--output", str(tmp_path / "report.json"), "--fail-on-regression",
    ]) == 0
