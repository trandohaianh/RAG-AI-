"""Evaluate user-curated evidence against an existing production RAG index.

The default validates JSON offline. Live retrieval requires --allow-external-calls:
query embeddings and configured reranking may incur provider charges. Generation
also requires --generate. No application DB, ingestion, recovery, Redis cache, or
LLM judge is used. Live evaluation requires both retrieval branches to succeed;
the serving path intentionally permits a surviving branch when its peer fails.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
DEFAULT_TEMPLATE = BACKEND / "evaluation" / "rag-real.template.json"
SCHEMA = BACKEND / "evaluation" / "rag-real.schema.json"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _positive_integer(value) -> bool:
    return type(value) is int and value > 0


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def load_dataset(path: Path) -> dict:
    """Validate without importing application config, providers, or databases."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_dataset(data)


def validate_dataset(data: dict) -> dict:
    """Validate an in-memory annotation without provider or configuration imports."""
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Expected evaluation schema_version 1")
    if data.get("kind") not in {"template", "user_curated"} or not _text(data.get("dataset_id")):
        raise ValueError("Dataset needs an ID and template/user_curated kind")
    if not isinstance(data.get("provenance", {}), dict) or data.get("provenance", {}).get(
        "origin", "unspecified"
    ) not in {"synthetic", "user_documents", "unspecified"}:
        raise ValueError("Unknown dataset origin")
    target = data.get("target")
    if not isinstance(target, dict) or not _positive_integer(target.get("user_id")):
        raise ValueError("Target needs a positive user_id")
    workspace = target.get("workspace_id")
    if workspace is not None and not _positive_integer(workspace):
        raise ValueError("workspace_id must be a positive integer or null")
    expected_collection = f"workspace_{workspace}" if workspace is not None else f"user_{target['user_id']}"
    if target.get("collection") != expected_collection:
        raise ValueError("Collection must match the target user/workspace scope")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("Dataset requires at least one case")
    seen = set()
    for case in cases:
        if not isinstance(case, dict) or not _text(case.get("id")) or not _text(case.get("query")):
            raise ValueError("Each case needs a nonempty ID and query")
        if case["id"] in seen:
            raise ValueError("Case IDs must be unique")
        seen.add(case["id"])
        if "source" in case and case["source"] is not None and not _text(case["source"]):
            raise ValueError("Optional source filter must be nonempty or null")
        if type(case.get("expect_no_evidence")) is not bool:
            raise ValueError("Each case needs an explicit boolean expect_no_evidence")
        if "reference_answer" in case and not _text(case["reference_answer"]):
            raise ValueError("Optional reference answer must be nonempty text")
        evidence = case.get("expected_evidence")
        if not isinstance(evidence, list) or bool(evidence) == case["expect_no_evidence"]:
            raise ValueError("Expected evidence and no-evidence expectation must be exclusive")
        identities = set()
        for item in evidence:
            if not isinstance(item, dict) or not _text(item.get("source")) or "page" not in item:
                raise ValueError("Evidence needs a source and explicit one-based page or null")
            page = item["page"]
            if page is not None and not _positive_integer(page):
                raise ValueError("Evidence page must be a positive integer or null")
            identity = (item["source"], page)
            if identity in identities:
                raise ValueError("Expected evidence identities must be unique")
            identities.add(identity)
            if case.get("source") and item["source"] != case["source"]:
                raise ValueError("Expected evidence must respect its case source filter")
        if "skip_reason" in case and not _text(case["skip_reason"]):
            raise ValueError("skip_reason must be a nonempty string")
    return data


def _implementation_hash() -> str:
    digest = hashlib.sha256()
    for path in [
        Path(__file__),
        SCHEMA,
        *[
            BACKEND / "rag" / name
            for name in (
                "retriever.py",
                "bm25.py",
                "rerank.py",
                "ingest.py",
                "document_lifecycle.py",
                "llm.py",
                "prompt.py",
                "chain.py",
                "evidence.py",
                "cache.py",
            )
        ],
    ]:
        digest.update(str(path.relative_to(BACKEND)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _runtime() -> dict:
    result = {"python": sys.version.split()[0]}
    for name in ("chromadb", "rank-bm25", "langchain-chroma", "langchain-google-genai", "google-genai"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            result[name] = None
    return result


@dataclass
class Snapshot:
    rows: list[tuple[str, str, int | None]]
    details: dict = field(default_factory=dict)


class LivePipeline:
    """Production building blocks, with strict branches and no cache/recovery."""

    def __init__(self, k: int):
        import config

        if not config.CHROMA_DIR.is_dir():
            raise ValueError("Configured Chroma index directory does not exist")
        self.index_dir = config.CHROMA_DIR
        self.k = k
        self.candidates = max(k, config.RETRIEVE_CANDIDATES)
        self.config = {
            "embedding_model": config.EMBEDDING_MODEL,
            "candidate_count": self.candidates,
            "rerank_policy": "cohere_with_lexical_fallback" if config.COHERE_API_KEY else "lexical",
            "rerank_model": config.COHERE_RERANK_MODEL if config.COHERE_API_KEY else None,
            "rerank_observation": "Configured policy only; the production reranker does not expose per-call fallback status.",
            "generation_provider": config.LLM_PROVIDER,
            "generation_model": config.OLLAMA_MODEL if config.LLM_PROVIDER == "ollama" else config.LLM_MODEL,
            "cache": "bypassed",
            "both_retrieval_branches_required": True,
        }

    def retrieve(self, target: dict, case: dict, k: int) -> Snapshot:
        import chromadb

        from rag import document_lifecycle as lifecycle
        from rag.bm25 import reciprocal_rank_fusion
        from rag.evidence import RetrievedChunks
        from rag.rerank import rerank_chunks
        from rag.retriever import _bm25_search_sync, _search_sync

        name = target["collection"]
        # Unlike collection_read_lock, this never invokes recovery. Evaluation
        # must not repair or publish user documents as a side effect.
        with lifecycle.storage_lock(self.index_dir, name, shared=True):
            lifecycle._check_uncertain(self.index_dir, name)
            if lifecycle._journal_path(self.index_dir, name).exists():
                raise lifecycle.DocumentRecoveryError(
                    "Pending publication: recover through normal application operations first"
                )
            # Explicit existence check prevents LangChain's get_or_create path
            # from creating a missing collection. Delete/publish uses this lock.
            client = chromadb.PersistentClient(path=str(self.index_dir))
            collection = client.get_collection(name)
            revision = lifecycle.read_revision(self.index_dir, name)
            lifecycle.sync_bm25_revision(self.index_dir, name, revision)
            args = (target["user_id"], case["query"], self.candidates, case.get("source"), target.get("workspace_id"))
            vector = _search_sync(*args)
            lexical = _bm25_search_sync(*args)
            raw = reciprocal_rank_fusion([vector, lexical], k=self.candidates)
            corpus_count = collection.count()
        ranked = rerank_chunks(case["query"], raw, top_n=k)
        return Snapshot(
            RetrievedChunks(ranked, collection=name, revision=revision),
            {
                "collection_revision": revision,
                "collection_chunks": corpus_count,
                "vector_candidates": len(vector),
                "bm25_candidates": len(lexical),
            },
        )

    def generate(self, query: str, rows: list) -> tuple[str, bool]:
        from rag.chain import _NO_DOC_MSG
        from rag.evidence import capture_evidence, evidence_context
        from rag.llm import generate_text
        from rag.prompt import build_gemini_prompt

        if not rows:
            return _NO_DOC_MSG, False
        answer = generate_text(build_gemini_prompt(evidence_context(capture_evidence(rows)), query), max_tokens=1024)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Generation returned no answer")
        return answer, True


def _safe_evidence(source: str, page, include_content: bool) -> dict:
    item = {"source_sha256": _sha(source), "page": page}
    if include_content:
        item["source"] = source
    return item


def _validate_rows(rows, k: int) -> list:
    if not isinstance(rows, (list, tuple)):
        raise ValueError("Retriever must return a sequence of rows")
    from rag.evidence import EvidenceChunk, RetrievedChunks

    checked = []
    for row in rows[:k]:
        if not isinstance(row, (list, tuple)) or len(row) != 3:
            raise ValueError("Retrieval rows must contain text, source, page")
        text, source, page = row
        if not _text(text) or not _text(source) or (page is not None and not _positive_integer(page)):
            raise ValueError("Invalid evidence row")
        checked.append(
            EvidenceChunk(
                text,
                source,
                page,
                chunk_id=getattr(row, "chunk_id", None),
                document_version=getattr(row, "document_version", None),
            )
        )
    return RetrievedChunks(
        checked, collection=getattr(rows, "collection", None), revision=getattr(rows, "revision", None)
    )


def evaluate(dataset: dict, *, mode="validate-only", k=5, pipeline=None, generate=False, include_content=False) -> dict:
    if not _positive_integer(k):
        raise ValueError("k must be positive")
    if mode not in {"validate-only", "retrieval"}:
        raise ValueError("Unsupported evaluation mode")
    if generate and mode != "retrieval":
        raise ValueError("Generation requires retrieval mode")
    if mode == "retrieval" and dataset["kind"] != "user_curated":
        raise ValueError("Template data cannot be executed; curate and mark the dataset user_curated first")
    if mode == "retrieval" and pipeline is None:
        raise ValueError("A live or explicitly injected pipeline is required")

    results = []
    recall_sum = mrr_sum = no_evidence_correct = 0.0
    supported_planned = no_evidence_planned = 0
    for case in dataset["cases"]:
        result = {
            "id": case["id"],
            "query_sha256": _sha(case["query"]),
            "expected_evidence": [
                _safe_evidence(item["source"], item["page"], include_content) for item in case["expected_evidence"]
            ],
            "expect_no_evidence": case["expect_no_evidence"],
        }
        if case.get("source"):
            result["source_filter_sha256"] = _sha(case["source"])
        if include_content:
            result["query"] = case["query"]
        if mode == "validate-only" or case.get("skip_reason"):
            result.update(
                {
                    "status": "skipped",
                    "retrieval_status": "not_run",
                    "skip_reason": "validation_only" if mode == "validate-only" else "dataset_skip",
                }
            )
            if include_content and case.get("skip_reason"):
                result["annotation_skip_reason"] = case["skip_reason"]
            results.append(result)
            continue

        expected = {(item["source"], item["page"]) for item in case["expected_evidence"]}
        supported_planned += bool(expected)
        no_evidence_planned += not expected
        started = perf_counter()
        try:
            snapshot = pipeline.retrieve(dataset["target"], case, k)
            if not isinstance(snapshot, Snapshot):
                raise ValueError("Pipeline must return a Snapshot")
            rows = _validate_rows(snapshot.rows, k)
            found = expected.intersection((source, page) for _, source, page in rows)
            recall = len(found) / len(expected) if expected else None
            rank = next((n for n, (_, source, page) in enumerate(rows, 1) if (source, page) in expected), None)
            mrr = 1 / rank if rank else 0.0
            hits = []
            for text, source, page in rows:
                hit = _safe_evidence(source, page, include_content)
                hit["text_sha256"] = _sha(text)
                if include_content:
                    hit["text"] = text
                hits.append(hit)
            # Only allow known non-content diagnostics from a pipeline.
            diagnostics = {
                key: snapshot.details[key]
                for key in ("collection_revision", "collection_chunks", "vector_candidates", "bm25_candidates")
                if key in snapshot.details
            }
            result.update(
                {
                    "status": "completed",
                    "retrieval_status": "completed",
                    "retrieval_passed": found == expected if expected else not rows,
                    "retrieved_evidence": hits,
                    "retrieval_diagnostics": diagnostics,
                    "evidence_recall_at_k": recall,
                    "reciprocal_rank": mrr if expected else None,
                    "empty_retrieval": not rows,
                }
            )
            # Failed retrievals never contribute evidence or successful no-evidence.
            recall_sum += recall or 0
            mrr_sum += mrr
            no_evidence_correct += int(not expected and not rows)
        except Exception as exc:
            result.update(
                {
                    "status": "failed",
                    "retrieval_status": "failed",
                    "retrieval_passed": False,
                    "error_type": type(exc).__name__,
                }
            )
            result["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
            results.append(result)
            continue
        if generate:
            try:
                answer, model_called = pipeline.generate(case["query"], rows)
                if not _text(answer) or type(model_called) is not bool:
                    raise ValueError("Invalid generation result")
                from rag.evidence import audit_citations, capture_evidence

                citation_audit = audit_citations(answer, capture_evidence(rows))
                if citation_audit["status"] == "invalid":
                    raise ValueError("Generation cited evidence outside its context")
                generation = {
                    "citation_audit": citation_audit,
                    "status": "completed",
                    "answer_sha256": _sha(answer),
                    "answer_characters": len(answer),
                    "model_called": model_called,
                    "review": {"status": "required", "correctness": None, "safety": None, "refusal": None},
                }
                if include_content:
                    generation["answer"] = answer
                result["generation"] = generation
            except Exception as exc:
                result["status"] = "failed"
                result["generation"] = {"status": "failed", "error_type": type(exc).__name__}
        result["elapsed_ms"] = round((perf_counter() - started) * 1000, 3)
        results.append(result)

    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode("utf-8")
    completed = sum(item["status"] == "completed" for item in results)
    failed = sum(item["status"] == "failed" for item in results)
    skipped = sum(item["status"] == "skipped" for item in results)
    retrieval_completed = sum(item.get("retrieval_status") == "completed" for item in results)
    retrieval_failed = sum(item.get("retrieval_status") == "failed" for item in results)
    generation_completed = sum(item.get("generation", {}).get("status") == "completed" for item in results)
    generation_failed = sum(item.get("generation", {}).get("status") == "failed" for item in results)
    return {
        "schema_version": 1,
        "report_type": "user_curated_rag_evaluation",
        "run_id": uuid.uuid4().hex,
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset["dataset_id"],
        "dataset_kind": dataset["kind"],
        "dataset_origin": dataset.get("provenance", {}).get("origin", "unspecified"),
        "dataset_sha256": hashlib.sha256(canonical).hexdigest(),
        "implementation_sha256": _implementation_hash(),
        "runtime": _runtime(),
        "target_collection_sha256": _sha(dataset["target"]["collection"]),
        "mode": mode,
        "includes_content": include_content,
        "k": k,
        "config": dict(pipeline.config) if mode == "retrieval" else None,
        "status": "validated" if mode == "validate-only" else "failed" if failed else "completed",
        "planned": len(results),
        "completed": completed,
        "failed": failed,
        "skipped": skipped,
        "retrieval": {
            "completed": retrieval_completed,
            "failed": retrieval_failed,
            "all_expectations_passed": all(
                item.get("retrieval_passed", False) for item in results if item["status"] != "skipped"
            )
            if mode == "retrieval" and completed + failed
            else None,
            "supported_planned": supported_planned,
            "no_evidence_planned": no_evidence_planned,
            "evidence_recall_at_k": round(recall_sum / supported_planned, 4) if supported_planned else None,
            "mrr_at_k": round(mrr_sum / supported_planned, 4) if supported_planned else None,
            "no_evidence_detection_accuracy": round(no_evidence_correct / no_evidence_planned, 4)
            if no_evidence_planned
            else None,
            "failure_policy": "Failed retrievals contribute zero in their planned denominator; dataset skips are excluded.",
        },
        "generation": {
            "status": "not_evaluated" if not generate else "requires_human_review",
            "planned": completed + failed if generate else 0,
            "completed": generation_completed,
            "failed": generation_failed,
            "skipped": retrieval_failed if generate else 0,
            "refusal_rate": None,
            "correctness": None,
            "safety": None,
            "note": "Empty retrieval and lexical overlap do not establish generated refusal, correctness, or safety.",
        },
        "limitations": [
            "Strict dual-branch retrieval differs from serving fallback; reranker may use its configured lexical fallback.",
            "Source/page matches measure annotated evidence retrieval, not answer correctness or safety.",
            "Collection revision is observed per case; legacy revisions do not fingerprint corpus contents.",
            "Chroma reads may maintain its internal metadata; no application DB, uploads, ingestion, or recovery is invoked.",
            "Default reports hash query/source/evidence/answer; choose non-sensitive dataset and case identifiers.",
        ],
        "results": results,
    }


def _report_path(output: Path, dataset_path: Path, *, data_roots=()) -> Path:
    output = output.resolve()
    if output.suffix.lower() != ".json" or output == dataset_path.resolve():
        raise ValueError("Report output must be a separate .json file")
    roots = (ROOT / "data", *data_roots)
    if any(output == root.resolve() or root.resolve() in output.parents for root in roots):
        raise ValueError("Report output must stay outside application data directories")
    return output


def _application_data_roots() -> tuple:
    # Called only after explicit live opt-in; validation-only stays independent
    # of .env loading, application config, SDKs, and database modules.
    from config import CHROMA_DIR, DATA_ROOT, DATABASE_PATH, UPLOAD_DIR

    return (DATA_ROOT, UPLOAD_DIR, CHROMA_DIR, DATABASE_PATH.parent)


def _write_report(output: Path, report: dict, dataset_path: Path, *, data_roots=()) -> None:
    output = _report_path(output, dataset_path, data_roots=data_roots)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--mode", choices=["validate-only", "retrieval"], default="validate-only")
    parser.add_argument(
        "--allow-external-calls",
        action="store_true",
        help="Acknowledge embedding/rerank/generation provider calls and possible charges",
    )
    parser.add_argument("--generate", action="store_true", help="Also generate bounded answers, requiring human review")
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="Include sensitive query, source, retrieved text, and answer for human review",
    )
    parser.add_argument("--output", type=Path, help="Fresh JSON report outside application data directories")
    parser.add_argument(
        "--review-manifest", type=Path, help="Verify annotation approvals and dev/holdout identity before execution"
    )
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)
    if args.k < 1:
        parser.error("--k must be positive")
    if args.generate and args.mode != "retrieval":
        parser.error("--generate requires --mode retrieval")
    if args.mode == "retrieval" and not args.allow_external_calls:
        parser.error(
            "Live retrieval requires --allow-external-calls; embeddings and configured reranking may incur charges"
        )
    if args.mode == "retrieval" and args.output is None:
        parser.error("Live evaluation requires --output")
    try:
        dataset = load_dataset(args.dataset)
        annotation_review = {"verified": False}
        if args.review_manifest is not None:
            from scripts.rag_review import read, verify_manifest

            annotation_review = verify_manifest(dataset, read(args.review_manifest))
        if args.mode == "retrieval" and dataset["kind"] != "user_curated":
            raise ValueError("Template data cannot be executed; curate and mark the dataset user_curated first")
        data_roots = _application_data_roots() if args.mode == "retrieval" else ()
        if args.output is not None:
            _report_path(args.output, args.dataset, data_roots=data_roots)
        pipeline = LivePipeline(args.k) if args.mode == "retrieval" else None
        report = evaluate(
            dataset,
            mode=args.mode,
            k=args.k,
            pipeline=pipeline,
            generate=args.generate,
            include_content=args.include_content,
        )
        report["annotation_review"] = annotation_review
        if args.output is not None:
            _write_report(args.output, report, args.dataset, data_roots=data_roots)
    except Exception as exc:
        # Provider/path/query messages can contain sensitive content. Error types
        # are sufficient for the default CLI; inspect locally for diagnosis.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__}), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {key: report[key] for key in ("status", "mode", "planned", "completed", "failed", "skipped")},
            ensure_ascii=False,
        )
    )
    return int(
        report["failed"] > 0
        or (
            args.fail_on_regression
            and args.mode == "retrieval"
            and report["retrieval"]["all_expectations_passed"] is not True
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
