"""Chạy bộ eval FieldOps và chặn hồi quy chất lượng.

Ví dụ:
  python backend/scripts/run_evals.py --mode offline                 # CI, không tốn tiền
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
    if baseline_path.exists() and not args.update_baseline and not args.limit:
        regressions, deltas = compare_baseline(report["metrics"], load_json(baseline_path)["metrics"], args.tolerance)
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
        baseline_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Đã cập nhật baseline: {baseline_path}")

    m = report["metrics"]
    print(f"[{report['mode']}] {m['cases']} câu · pass {m['pass_rate']} · recall@{args.k} {m['retrieval_recall_at_k']} · "
          f"MRR {m['retrieval_mrr']} · phủ dữ kiện {m['fact_coverage']} · từ chối đúng {m['refusal_accuracy']} · "
          f"vi phạm {m['safety_violations']} · ${m['cost_usd']}")
    print(f"Báo cáo: {stem.with_suffix('.md')}")
    for line in gate_failures + regressions:
        print(f"  ✗ {line}")
    if args.no_gates:
        return 0
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
