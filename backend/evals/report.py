"""Ngưỡng chất lượng (gates), so sánh baseline và xuất báo cáo Markdown."""
from __future__ import annotations

import json
from pathlib import Path

# Chỉ số "càng cao càng tốt" và "càng thấp càng tốt" để so baseline đúng chiều.
HIGHER_IS_BETTER = [
    "pass_rate", "retrieval_recall_at_k", "retrieval_mrr", "retrieval_ndcg_at_k",
    "fact_coverage", "groundedness", "refusal_accuracy", "citation_present_rate",
]
LOWER_IS_BETTER = ["false_refusal_rate", "safety_violations", "citation_invalid_count", "errors"]

LABELS = {
    "pass_rate": "Tỉ lệ câu đạt",
    "retrieval_recall_at_k": "Recall@k (truy xuất)",
    "retrieval_precision_at_k": "Precision@k",
    "retrieval_mrr": "MRR",
    "retrieval_ndcg_at_k": "nDCG@k",
    "retrieval_hit_rate": "Hit rate",
    "fact_coverage": "Độ phủ dữ kiện",
    "groundedness": "Bám nguồn (lexical)",
    "refusal_accuracy": "Từ chối đúng (câu không có đáp án)",
    "false_refusal_rate": "Từ chối nhầm (câu có đáp án)",
    "citation_present_rate": "Có trích dẫn 【E#】",
    "citation_invalid_count": "Trích dẫn sai nhãn",
    "safety_violations": "Vi phạm an toàn",
    "errors": "Lỗi pipeline",
    "judge_faithfulness": "LLM-judge: faithfulness",
    "judge_relevancy": "LLM-judge: relevancy",
    "latency_p50_ms": "Độ trễ p50 (ms)",
    "latency_p95_ms": "Độ trễ p95 (ms)",
    "tokens_in": "Token vào",
    "tokens_out": "Token ra",
    "cost_usd": "Chi phí (USD)",
    "cost_per_case_usd": "Chi phí / câu (USD)",
}


def check_gates(metrics: dict, thresholds: dict) -> list[str]:
    """thresholds: {"min": {metric: value}, "max": {metric: value}}"""
    failures = []
    for name, floor in (thresholds.get("min") or {}).items():
        value = metrics.get(name)
        if value is None or value < floor:
            failures.append(f"{name}={value} < {floor}")
    for name, ceiling in (thresholds.get("max") or {}).items():
        value = metrics.get(name)
        if value is None or value > ceiling:
            failures.append(f"{name}={value} > {ceiling}")
    return failures


def compare_baseline(metrics: dict, baseline: dict, tolerance: float = 0.03) -> tuple[list[str], dict]:
    """Báo hồi quy khi chỉ số tụt quá `tolerance` so với baseline đã lưu."""
    regressions, deltas = [], {}
    for name in HIGHER_IS_BETTER + LOWER_IS_BETTER:
        new, old = metrics.get(name), baseline.get(name)
        if new is None or old is None:
            continue
        delta = round(new - old, 4)
        deltas[name] = delta
        if name in HIGHER_IS_BETTER and delta < -tolerance:
            regressions.append(f"{name}: {old} → {new} ({delta:+})")
        if name in LOWER_IS_BETTER and delta > (0 if isinstance(old, int) else tolerance):
            regressions.append(f"{name}: {old} → {new} ({delta:+})")
    return regressions, deltas


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.4f}" if abs(value) < 1 else f"{value:,.2f}"
    return f"{value:,}" if isinstance(value, int) else str(value)


def to_markdown(report: dict, gate_failures: list[str], regressions: list[str], deltas: dict) -> str:
    m = report["metrics"]
    status = "✅ ĐẠT" if not gate_failures and not regressions else "❌ KHÔNG ĐẠT"
    cfg = report["config"]
    lines = [
        f"# FieldOps RAG eval — {report['mode']} — {status}",
        "",
        f"- Dataset: `{report['dataset_id']}` · split `{report['split']}` · {m['cases']} câu",
        f"- Thời điểm: {report['started_at']} · thời gian chạy {report['duration_s']}s",
        f"- Cấu hình: LLM `{cfg['llm_model']}` · embedding `{cfg['embedding_model']}` · rerank `{cfg['rerank']}` · k={cfg['k']} · chunk {cfg['chunk_size']}/{cfg['chunk_overlap']}",
        f"- Ingest {len(report['corpus']['files'])} tài liệu: {report['corpus']['ingest_s']}s, {report['corpus']['ingest_tokens']:,} token, ${report['corpus']['ingest_cost_usd']}",
    ]
    if report["mode"] == "offline":
        lines.append("- ⚠️ Offline dùng embedding băm + bộ sinh trích xuất: đo đường ống truy xuất và bộ chấm, KHÔNG đo chất lượng Gemini.")
    lines += ["", "## Chỉ số tổng", "", "| Chỉ số | Giá trị | Δ so baseline |", "|---|---:|---:|"]
    for key, label in LABELS.items():
        if key in m:
            delta = deltas.get(key)
            lines.append(f"| {label} | {_fmt(m[key])} | {'' if delta is None else f'{delta:+.4f}'} |")
    if gate_failures:
        lines += ["", "## Không đạt ngưỡng", ""] + [f"- {f}" for f in gate_failures]
    if regressions:
        lines += ["", "## Hồi quy so với baseline", ""] + [f"- {r}" for r in regressions]

    lines += ["", "## Theo nhóm câu hỏi", "", "| Nhóm | Số câu | Đạt | Recall@k | Phủ dữ kiện | Từ chối đúng | Vi phạm |", "|---|---:|---:|---:|---:|---:|---:|"]
    for cat, cm in report["by_category"].items():
        lines.append(f"| {cat} | {cm['cases']} | {_fmt(cm['pass_rate'])} | {_fmt(cm['retrieval_recall_at_k'])} | "
                     f"{_fmt(cm['fact_coverage'])} | {_fmt(cm['refusal_accuracy'])} | {cm['safety_violations']} |")

    failed = [c for c in report["cases"] if not c["passed"]]
    if failed:
        lines += ["", f"## Câu không đạt ({len(failed)})", ""]
        for c in failed[:40]:
            answer = (c["answer"] or "").replace("\n", " ")
            lines.append(f"- **{c['id']}** [{c['category']}] {', '.join(c['reasons'])} — _{c['query']}_")
            if c["missing_facts"]:
                lines.append(f"  - thiếu: {c['missing_facts']}")
            if c["forbidden_hits"]:
                lines.append(f"  - nội dung cấm: {c['forbidden_hits']}")
            lines.append(f"  - nguồn lấy được: {c['retrieved_sources'][:3]} · kỳ vọng: {c['expected_sources']}")
            lines.append(f"  - trả lời: {answer[:220]}{'…' if len(answer) > 220 else ''}")
    return "\n".join(lines) + "\n"


def history_row(report: dict, passed: bool) -> str:
    m = report["metrics"]
    keep = {k: m.get(k) for k in HIGHER_IS_BETTER + LOWER_IS_BETTER + ["latency_p95_ms", "cost_usd", "cases"]}
    return json.dumps({"at": report["started_at"], "mode": report["mode"], "dataset": report["dataset_id"],
                       "split": report["split"], "llm": report["config"]["llm_model"], "passed": passed,
                       **keep}, ensure_ascii=False)


def load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
