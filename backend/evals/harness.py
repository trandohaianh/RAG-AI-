"""Chạy golden set qua pipeline RAG của ứng dụng và tổng hợp chỉ số.

QUAN TRỌNG: gọi `prepare_environment()` TRƯỚC khi import bất kỳ module ứng dụng nào,
vì config.py đọc DATA_DIR/REDIS_URL... ngay lúc import.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

EVAL_USER_ID = 900_001
BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent


@dataclass
class EvalContext:
    mode: str
    data_dir: Path
    usage_factory: object = None
    cleanup: list = field(default_factory=list)


def prepare_environment(mode: str) -> EvalContext:
    if mode not in {"offline", "live"}:
        raise ValueError("mode must be offline or live")
    data_dir = Path(tempfile.mkdtemp(prefix="fieldops-eval-"))
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["MEDALLION_ENABLED"] = "false"
    os.environ["REDIS_URL"] = ""  # không để cache làm sai độ trễ/kết quả
    if mode == "offline":
        os.environ["COHERE_API_KEY"] = ""
        os.environ["LLM_PROVIDER"] = "gemini"  # bị thay bằng bộ sinh trích xuất bên dưới
    return EvalContext(mode=mode, data_dir=data_dir, cleanup=[data_dir])


def _install_providers(ctx: EvalContext) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import rag.chain as chain
    import rag.ingest as ingest
    from costs import recorder
    from costs import models as _cost_models  # noqa: F401
    from database import Base
    from rag.embed_utils import MeteredEmbeddings

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[_cost_models.LlmUsageEvent.__table__])
    ctx.usage_factory = sessionmaker(bind=engine)
    recorder.set_session_factory(ctx.usage_factory)

    if ctx.mode == "offline":
        from evals.offline_providers import HashingEmbeddings, fake_generate_text

        ingest._embeddings = lambda: MeteredEmbeddings(HashingEmbeddings(), "local-hashing")  # type: ignore
        chain.generate_text = fake_generate_text  # type: ignore
    else:
        from config import GEMINI_API_KEY, LLM_PROVIDER

        if LLM_PROVIDER != "ollama" and not GEMINI_API_KEY:
            raise SystemExit("Chế độ live cần GEMINI_API_KEY (hoặc LLM_PROVIDER=ollama)")


def load_dataset(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != 2 or not data.get("cases"):
        raise ValueError("Cần golden set schema_version 2")
    ids = [c["id"] for c in data["cases"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Trùng id câu hỏi")
    for case in data["cases"]:
        if not case.get("query", "").strip():
            raise ValueError(f"{case['id']}: thiếu query")
        if case["expect_refusal"] and case.get("expected_sources"):
            raise ValueError(f"{case['id']}: câu phải từ chối thì không có nguồn kỳ vọng")
        if not case["expect_refusal"] and not case.get("expected_sources"):
            raise ValueError(f"{case['id']}: câu có đáp án cần nguồn kỳ vọng")
        for group in case.get("facts", []):
            if not isinstance(group, list) or not group:
                raise ValueError(f"{case['id']}: facts phải là danh sách nhóm không rỗng")
    return data


def _ingest_corpus(dataset: dict, ctx: EvalContext) -> list[str]:
    from rag.ingest import _ingest_file_sync
    from costs.context import usage_scope

    corpus_dir = ROOT / dataset["corpus_dir"]
    files = sorted(corpus_dir.glob(dataset.get("corpus_glob", "*")))
    if not files:
        raise ValueError(f"Không có tài liệu trong {corpus_dir}")
    upload = ctx.data_dir / "uploads" / str(EVAL_USER_ID)
    upload.mkdir(parents=True, exist_ok=True)
    names = []
    with usage_scope(user_id=EVAL_USER_ID, feature="eval_ingest"):
        for src in files:
            dest = upload / src.name
            shutil.copyfile(src, dest)
            _ingest_file_sync(EVAL_USER_ID, dest)
            names.append(src.name)
    return names


def _usage_since(ctx: EvalContext, last_id: int) -> tuple[list, int]:
    from costs.models import LlmUsageEvent

    with ctx.usage_factory() as db:  # type: ignore[operator]
        rows = db.query(LlmUsageEvent).filter(LlmUsageEvent.id > last_id).order_by(LlmUsageEvent.id).all()
        events = [
            {"feature": r.feature, "model": r.model, "input": r.input_tokens,
             "output": r.output_tokens + r.thinking_tokens, "cost": r.cost_usd,
             "latency_ms": r.latency_ms or 0, "estimated": r.estimated}
            for r in rows
        ]
        return events, (rows[-1].id if rows else last_id)


def _judge(question: str, context: str, answer: str) -> dict | None:
    from rag.judge import _JSON_RE, _JUDGE_PROMPT
    from rag.llm import generate_text

    prompt = _JUDGE_PROMPT.format(context=context[:6000], question=question, answer=answer[:3000])
    try:
        # Cùng ngân sách token với judge production (rag/judge.py): 256 token làm model có
        # "thinking" trả rỗng, và các ca đó lặng lẽ rơi khỏi trung bình judge.
        raw = generate_text(prompt, json_mode=True, temperature=0.0, max_tokens=1024, feature="eval_judge")
        match = _JSON_RE.search(raw)
        data = json.loads(match.group(0)) if match else None
        if not data:
            return None
        clamp = lambda value: min(1.0, max(0.0, float(value)))  # noqa: E731 — như production
        return {"faithfulness": clamp(data["faithfulness"]), "relevancy": clamp(data["relevancy"])}
    except Exception:
        return None


def _grade(case: dict, answer: str, bundle: dict, k: int, error: str | None) -> dict:
    from evals import graders

    evidence = bundle.get("evidence") or []
    ranked = [item["source"] for item in evidence]
    contexts = [item["quote"] for item in evidence]
    audit = (bundle.get("citation_audit") or {}).get("status")
    refused = graders.is_refusal(answer) if answer else False
    coverage, missing = graders.fact_coverage(answer, case.get("facts", []))
    forbidden = graders.forbidden_hits(answer, case.get("forbidden", []))
    retrieval = graders.retrieval_scores(ranked, case.get("expected_sources", []), k)
    grounded = None if refused else graders.groundedness(answer, contexts)

    reasons = []
    if error:
        reasons.append(f"error:{error}")
    if forbidden:
        reasons.append("forbidden_content")
    if case["expect_refusal"]:
        if not refused:
            reasons.append("should_refuse")
    else:
        if refused:
            reasons.append("false_refusal")
        if coverage is not None and coverage < 0.5:
            reasons.append("low_fact_coverage")
        if audit == "invalid":
            reasons.append("invalid_citation")
    return {
        "id": case["id"], "category": case.get("category", "other"), "split": case.get("split", "dev"),
        "query": case["query"], "answer": answer, "passed": not reasons, "reasons": reasons,
        "refused": refused, "expect_refusal": case["expect_refusal"],
        "fact_coverage": coverage, "missing_facts": missing, "forbidden_hits": forbidden,
        "citation_status": audit, "groundedness": grounded, "retrieval": retrieval,
        "retrieved_sources": graders.ranked_unique_sources(ranked),
        "expected_sources": case.get("expected_sources", []),
    }


def run_suite(ctx: EvalContext, dataset_path: Path, *, k: int = 5, judge: bool = False,
              split: str | None = None, limit: int | None = None) -> dict:
    _install_providers(ctx)
    import config
    from costs.context import usage_scope
    from rag.chain import RagGenerationError, answer_rag_once

    if k > config.TOP_K:
        # answer_rag_once luôn truy hồi TOP_K chunk; k lớn hơn chỉ làm recall@k bị báo thấp
        # và gắn nhãn sai trong báo cáo.
        raise ValueError(f"k={k} lớn hơn TOP_K={config.TOP_K}: đặt TOP_K>=k trước khi chạy eval")

    dataset = load_dataset(dataset_path)
    cases = [c for c in dataset["cases"] if split in (None, "all") or c.get("split") == split]
    if limit:
        cases = cases[:limit]

    started = time.perf_counter()
    corpus = _ingest_corpus(dataset, ctx)
    ingest_events, last_id = _usage_since(ctx, 0)
    ingest_s = time.perf_counter() - started

    results = []
    for case in cases:
        bundle: dict = {}
        answer, error = "", None
        t0 = time.perf_counter()
        with usage_scope(user_id=EVAL_USER_ID, feature="eval"):
            try:
                answer, _ = asyncio.run(answer_rag_once(EVAL_USER_ID, case["query"], evidence_out=bundle))
            except RagGenerationError:
                error = "generation_failed"
            except Exception as exc:  # pipeline lỗi vẫn ghi nhận, không dừng cả bộ
                error = type(exc).__name__
        latency = (time.perf_counter() - t0) * 1000
        graded = _grade(case, answer, bundle, k, error)
        if judge and ctx.mode == "live" and answer and not graded["refused"]:
            context = "\n\n".join(item["quote"] for item in bundle.get("evidence") or [])
            with usage_scope(user_id=EVAL_USER_ID):
                graded["judge"] = _judge(case["query"], context, answer)
        events, last_id = _usage_since(ctx, last_id)
        graded["latency_ms"] = round(latency, 1)
        graded["llm_latency_ms"] = sum(e["latency_ms"] for e in events if e["feature"] not in {"embed_query", "eval_judge"})
        graded["tokens_in"] = sum(e["input"] for e in events)
        graded["tokens_out"] = sum(e["output"] for e in events)
        graded["cost_usd"] = round(sum(e["cost"] for e in events), 8)
        results.append(graded)

    report = {
        "schema_version": 1,
        "suite": "fieldops-rag-eval",
        "mode": ctx.mode,
        "dataset_id": dataset["dataset_id"],
        "dataset_path": str(Path(dataset_path).resolve().relative_to(ROOT)) if str(Path(dataset_path).resolve()).startswith(str(ROOT)) else str(dataset_path),
        "split": split or "all",
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "duration_s": round(time.perf_counter() - started, 2),
        "config": _config_snapshot(ctx, k, judge),
        "corpus": {"files": corpus, "ingest_s": round(ingest_s, 2),
                   "ingest_cost_usd": round(sum(e["cost"] for e in ingest_events), 8),
                   "ingest_tokens": sum(e["input"] for e in ingest_events)},
        "metrics": aggregate(results),
        "by_category": {cat: aggregate(rows) for cat, rows in _group(results, "category").items()},
        "by_split": {sp: aggregate(rows) for sp, rows in _group(results, "split").items()},
        "cases": results,
    }
    return report


def _config_snapshot(ctx: EvalContext, k: int, judge: bool) -> dict:
    import config

    offline = ctx.mode == "offline"
    return {
        "k": k, "retrieve_candidates": config.RETRIEVE_CANDIDATES, "chunk_size": config.CHUNK_SIZE,
        "chunk_overlap": config.CHUNK_OVERLAP,
        "rerank": config.COHERE_RERANK_MODEL if os.environ.get("COHERE_API_KEY") else "lexical",
        "llm_model": "local-extractive" if offline else config.LLM_MODEL,
        "embedding_model": "local-hashing" if offline else config.EMBEDDING_MODEL,
        "judge": judge,
    }


def _group(rows: list[dict], key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row[key]].append(row)
    return dict(sorted(grouped.items()))


def _mean(values) -> float | None:
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def aggregate(rows: list[dict]) -> dict:
    from evals.graders import percentile

    answerable = [r for r in rows if not r["expect_refusal"]]
    unanswerable = [r for r in rows if r["expect_refusal"]]
    retrieval = [r["retrieval"] for r in answerable if r["retrieval"]]
    latencies = [r.get("latency_ms", 0.0) for r in rows]
    judged = [r["judge"] for r in rows if r.get("judge")]
    return {
        "cases": len(rows),
        "pass_rate": _mean([1.0 if r["passed"] else 0.0 for r in rows]),
        "retrieval_recall_at_k": _mean([m["recall"] for m in retrieval]),
        "retrieval_precision_at_k": _mean([m["precision"] for m in retrieval]),
        "retrieval_mrr": _mean([m["mrr"] for m in retrieval]),
        "retrieval_ndcg_at_k": _mean([m["ndcg"] for m in retrieval]),
        "retrieval_hit_rate": _mean([m["hit"] for m in retrieval]),
        "fact_coverage": _mean([r["fact_coverage"] for r in answerable]),
        "groundedness": _mean([r["groundedness"] for r in answerable]),
        "refusal_accuracy": _mean([1.0 if r["refused"] else 0.0 for r in unanswerable]),
        "false_refusal_rate": _mean([1.0 if r["refused"] else 0.0 for r in answerable]),
        "citation_present_rate": _mean([1.0 if r["citation_status"] == "cited" else 0.0 for r in answerable]),
        "citation_invalid_count": sum(1 for r in rows if r["citation_status"] == "invalid"),
        "safety_violations": sum(1 for r in rows if r["forbidden_hits"]),
        "errors": sum(1 for r in rows if any(x.startswith("error:") for x in r["reasons"])),
        "judge_faithfulness": _mean([j["faithfulness"] for j in judged]),
        "judge_relevancy": _mean([j["relevancy"] for j in judged]),
        "latency_p50_ms": round(percentile(latencies, 0.5) or 0, 1),
        "latency_p95_ms": round(percentile(latencies, 0.95) or 0, 1),
        "tokens_in": sum(r.get("tokens_in", 0) for r in rows),
        "tokens_out": sum(r.get("tokens_out", 0) for r in rows),
        "cost_usd": round(sum(r.get("cost_usd", 0.0) for r in rows), 6),
        "cost_per_case_usd": round(sum(r.get("cost_usd", 0.0) for r in rows) / len(rows), 8) if rows else 0.0,
    }


def cleanup(ctx: EvalContext) -> None:
    for path in ctx.cleanup:
        shutil.rmtree(path, ignore_errors=True)
