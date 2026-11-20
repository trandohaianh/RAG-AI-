"""Repeatable synthetic FieldOps lexical retrieval regression benchmark.

Default/offline mode calls BM25 only: no embeddings, vector database, LLM, judge,
or paid API. It does not measure production RAG accuracy or generated refusal.
Every invocation starts a fresh report and ignores legacy eval_results.partial.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

from rag.bm25 import BM25_AVAILABLE, bm25_search, invalidate

DEFAULT_DATASET = BACKEND / "evaluation" / "fieldops-v1.json"


def load_dataset(path: Path) -> dict:
    dataset = json.loads(path.read_text(encoding="utf-8"))
    if dataset.get("schema_version") != 1 or not dataset.get("dataset_id"):
        raise ValueError("Unsupported or unidentified evaluation dataset")
    documents, cases = dataset.get("documents", []), dataset.get("cases", [])
    if not documents or not cases:
        raise ValueError("Dataset requires documents and cases")
    ids = [doc["id"] for doc in documents]
    identities = [(doc["text"], doc["source"], doc["page"]) for doc in documents]
    if len(ids) != len(set(ids)) or len(identities) != len(set(identities)):
        raise ValueError("Document IDs and evidence identities must be unique")
    if any(
        (doc["page"] is not None and (type(doc["page"]) is not int or doc["page"] < 1)) or not doc["text"].strip()
        for doc in documents
    ):
        raise ValueError("Evidence needs nonempty text and a one-based positive page or null for unpaged text")
    case_ids = [case["id"] for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Case IDs must be unique")
    known = set(ids)
    for case in cases:
        expected = case["expected_evidence"]
        if not case["query"].strip() or not set(expected).issubset(known) or len(expected) != len(set(expected)):
            raise ValueError("Invalid query or expected evidence")
        if bool(expected) == bool(case.get("expect_no_evidence", False)):
            raise ValueError("Each case needs evidence or an explicit no-evidence expectation")
    return dataset


class FixtureCollection:
    def __init__(self, documents):
        self.documents = documents

    def get(self, include=None):
        return {
            "documents": [doc["text"] for doc in self.documents],
            "metadatas": [
                {"source": doc["source"], "page": doc["page"] - 1 if doc["page"] is not None else None}
                for doc in self.documents
            ],
        }


def _implementation_hash() -> str:
    digest = hashlib.sha256()
    for path in [Path(__file__), BACKEND / "rag" / "bm25.py", BACKEND / "rag" / "evidence.py"]:
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_offline(dataset_path: Path = DEFAULT_DATASET, k: int = 3, search=None) -> dict:
    if k < 1:
        raise ValueError("k must be positive")
    dataset = load_dataset(Path(dataset_path))
    if search is None and not BM25_AVAILABLE:
        raise RuntimeError("rank_bm25 is required; refusing to report an unavailable retriever")
    collection = FixtureCollection(dataset["documents"])
    run_id = uuid.uuid4().hex
    collection_name = "fieldops-eval-" + run_id
    invalidate(collection_name)
    if search is None:

        def search(query, limit, source):
            return bm25_search(collection, collection_name, query, limit, source, raise_errors=True)

    evidence_ids = {(doc["text"], doc["source"], doc["page"]): doc["id"] for doc in dataset["documents"]}
    results = []
    supported_count = sum(bool(case["expected_evidence"]) for case in dataset["cases"])
    no_evidence_count = len(dataset["cases"]) - supported_count
    recall_sum = mrr_sum = no_evidence_correct = 0
    for case in dataset["cases"]:
        result = {
            "id": case["id"],
            "query": case["query"],
            "expected_evidence": case["expected_evidence"],
            "expect_no_evidence": case.get("expect_no_evidence", False),
        }
        try:
            rows = search(case["query"], k, case.get("source"))[:k]
            retrieved = [evidence_ids.get(tuple(row)) for row in rows]
            expected = set(case["expected_evidence"])
            found = expected.intersection(retrieved)
            recall = len(found) / len(expected) if expected else None
            rank = next((i for i, item in enumerate(retrieved, 1) if item in expected), None)
            reciprocal_rank = 1 / rank if rank else 0.0
            passed = found == expected if expected else not rows
            result.update(
                {
                    "status": "completed",
                    "passed": passed,
                    "retrieved_evidence": retrieved,
                    "retrieved_sources": [{"source": row[1], "page": row[2]} for row in rows],
                    "evidence_recall_at_k": recall,
                    "reciprocal_rank": reciprocal_rank if expected else None,
                    "no_evidence_detected": not rows,
                }
            )
            # Account only after the whole result is constructed successfully.
            # A malformed hit must not contribute to a case reported as failed.
            recall_sum += recall or 0
            mrr_sum += reciprocal_rank
            no_evidence_correct += int(not expected and not rows)
        except Exception as exc:
            result.update({"status": "failed", "passed": False, "error_type": type(exc).__name__})
        results.append(result)

    completed = sum(result["status"] == "completed" for result in results)
    failed = len(results) - completed
    all_passed = all(result["passed"] for result in results)
    canonical = json.dumps(dataset, ensure_ascii=False, sort_keys=True).encode("utf-8")
    try:
        bm25_version = version("rank-bm25")
    except PackageNotFoundError:
        bm25_version = None
    return {
        "schema_version": 1,
        "run_id": run_id,
        "runtime": {"python": sys.version.split()[0], "rank_bm25": bm25_version},
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset_id": dataset["dataset_id"],
        "dataset_sha256": hashlib.sha256(canonical).hexdigest(),
        "implementation_sha256": _implementation_hash(),
        "mode": "offline",
        "scope": "synthetic BM25-only retrieval",
        "model": None,
        "k": k,
        "status": "completed" if not failed else "failed",
        "planned": len(dataset["cases"]),
        "completed": completed,
        "failed": failed,
        "skipped": 0,
        "all_expectations_passed": all_passed,
        "metrics": {
            "supported_planned": supported_count,
            "no_evidence_planned": no_evidence_count,
            "evidence_recall_at_k": round(recall_sum / supported_count, 4) if supported_count else None,
            "mrr_at_k": round(mrr_sum / supported_count, 4) if supported_count else None,
            "no_evidence_detection_accuracy": round(no_evidence_correct / no_evidence_count, 4)
            if no_evidence_count
            else None,
            "failure_policy": "Failed cases count as zero in their planned denominator.",
        },
        "generation": {
            "status": "not_evaluated",
            "refusal_rate": None,
            "reason": "No answer generation or judge was invoked. Empty retrieval is not proof of generated refusal.",
        },
        "limitations": "Authored synthetic fixture; no production vector, rerank, generation, safety, or semantic accuracy claim.",
        "results": results,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="BM25-only mode (also the default)")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path, default=ROOT / "fieldops_eval_results.json")
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args(argv)
    report = run_offline(args.dataset, args.k)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {key: report[key] for key in ["scope", "planned", "completed", "failed", "metrics"]}, ensure_ascii=False
        )
    )
    print(f"Report: {args.output}")
    return int(args.fail_on_regression and not report["all_expectations_passed"])


if __name__ == "__main__":
    raise SystemExit(main())
