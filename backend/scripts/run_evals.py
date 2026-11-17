"""Chạy bộ eval FieldOps và chặn hồi quy chất lượng.

Ví dụ:
  python backend/scripts/run_evals.py --mode offline --split dev --no-gates   # chỉnh tham số trên dev
  python backend/scripts/run_evals.py --mode offline --split holdout          # CI: chỉ chặn trên holdout
  python backend/scripts/run_evals.py --mode live --judge --split holdout   # dùng Gemini thật
  python backend/scripts/run_evals.py --mode offline --update-baseline     # chốt mốc mới

Mã thoát: 0 đạt; 1 không đạt ngưỡng hoặc hồi quy; 2 lỗi cấu hình.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

DEFAULT_DATASET = BACKEND / "evals" / "datasets" / "fieldops-sop-golden-v1.json"
THRESHOLDS = BACKEND / "evals" / "thresholds.json"
BASELINES = BACKEND / "evals" / "baselines"


def holdout_fingerprint(dataset_path: Path) -> str:
    """Dấu vân tay các câu holdout. Holdout phải ĐÓNG BĂNG: nếu được sửa cho khớp hệ thống thì
    nó không còn đo được tổng quát hoá (rò rỉ). Baseline holdout lưu dấu này; CI so lại."""
    import hashlib
    import json

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = sorted((c for c in dataset["cases"] if c.get("split") == "holdout"), key=lambda c: c["id"])
    return hashlib.sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def expert_review_status(dataset_path: Path) -> dict:
    """Bao nhiêu câu holdout đã có chuyên gia nghiệp vụ duyệt (reviewed_by + reviewed_at)."""
    import json

    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    holdout = [c for c in dataset["cases"] if c.get("split") == "holdout"]
    reviewed = [c for c in holdout if c.get("reviewed_by") and c.get("reviewed_at")]
    return {"holdout_cases": len(holdout), "expert_reviewed": len(reviewed), "target": 100}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--split", choices=["all", "dev", "holdout"], default="all")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--limit", type=int, default=None, help="Chỉ chạy N câu đầu (thử nhanh)")
    parser.add_argument("--judge", action="store_true", help="Thêm LLM-judge (chỉ live, tốn thêm token)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "evals")
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--tolerance", type=float, default=0.03)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--no-gates", action="store_true", help="Chỉ báo cáo, không trả mã lỗi")
    args = parser.parse_args(argv)

    if args.mode == "live" and os.environ.get("FIELDOPS_EVAL_ALLOW_LIVE", "1") == "0":
        print("Live eval bị tắt bởi FIELDOPS_EVAL_ALLOW_LIVE=0", file=sys.stderr)
        return 2

    from evals.harness import cleanup, prepare_environment

    ctx = prepare_environment(args.mode)  # phải chạy trước khi import module ứng dụng
    try:
        from evals.harness import run_suite
        from evals.report import (check_gates, compare_baseline, history_row, load_json, to_markdown)

        report = run_suite(ctx, args.dataset, k=args.k, judge=args.judge,
                           split=args.split, limit=args.limit)
    finally:
        cleanup(ctx)

    thresholds = load_json(THRESHOLDS).get(args.mode, {}) if THRESHOLDS.exists() else {}
    gate_failures = [] if args.limit else check_gates(report["metrics"], thresholds)
    baseline_path = args.baseline or BASELINES / f"{args.mode}-{args.split}.json"
    regressions, deltas = [], {}
    # Một câu trên tập n câu là 1/n điểm: dung sai nhỏ hơn mức đó thì mọi dao động một câu đều
    # thành "hồi quy" (holdout 12 câu: 1 câu = 0.083 > 0.03).
    cases = report["metrics"].get("cases") or 1
    tolerance = max(args.tolerance, round(1 / cases, 4))
    fingerprint = holdout_fingerprint(args.dataset) if args.split == "holdout" else None
    if baseline_path.exists() and not args.update_baseline and not args.limit:
        baseline_doc = load_json(baseline_path)
        regressions, deltas = compare_baseline(report["metrics"], baseline_doc["metrics"], tolerance)
        frozen = baseline_doc.get("holdout_fingerprint")
        if fingerprint and frozen and frozen != fingerprint:
            regressions.append("holdout đã bị sửa so với baseline đóng băng — tạo phiên bản dataset mới "
                               "và chốt baseline có chủ đích (--update-baseline), không sửa holdout cũ")
    review = expert_review_status(args.dataset)
    report["expert_review"] = review
    passed = not gate_failures and not regressions

    import json

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    stem = args.output_dir / f"{stamp}-{args.mode}-{args.split}"
    report["gates"] = {"passed": passed, "threshold_failures": gate_failures, "regressions": regressions,
                       "baseline": str(baseline_path.relative_to(ROOT)) if baseline_path.exists() else None}
    stem.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    markdown = to_markdown(report, gate_failures, regressions, deltas)
    stem.with_suffix(".md").write_text(markdown, encoding="utf-8")
    (args.output_dir / "latest.md").write_text(markdown, encoding="utf-8")
    with (args.output_dir / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(history_row(report, passed) + "\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(markdown)

    if args.update_baseline:
        BASELINES.mkdir(parents=True, exist_ok=True)
        baseline = {"dataset_id": report["dataset_id"], "mode": report["mode"], "split": report["split"],
                    "created_at": report["started_at"], "config": report["config"], "metrics": report["metrics"]}
        if fingerprint:
            baseline["holdout_fingerprint"] = fingerprint
        baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Đã cập nhật baseline: {baseline_path}")

    m = report["metrics"]
    print(f"[{report['mode']}] {m['cases']} câu · pass {m['pass_rate']} · recall@{args.k} {m['retrieval_recall_at_k']} · "
          f"MRR {m['retrieval_mrr']} · phủ dữ kiện {m['fact_coverage']} · từ chối đúng {m['refusal_accuracy']} · "
          f"vi phạm {m['safety_violations']} · ${m['cost_usd']}")
    print(f"Báo cáo: {stem.with_suffix('.md')} · dung sai hồi quy {tolerance}")
    print(f"Holdout đã được chuyên gia duyệt: {review['expert_reviewed']}/{review['holdout_cases']} "
          f"(mục tiêu ≥{review['target']} câu)")
    for line in gate_failures + regressions:
        print(f"  ✗ {line}")
    if args.no_gates:
        return 0
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
