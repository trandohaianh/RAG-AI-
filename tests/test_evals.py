"""Bộ eval tự viết: bộ chấm, cổng chất lượng, và chạy thử end-to-end ở chế độ offline."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evals import graders
from evals.harness import load_dataset
from evals.report import check_gates, compare_baseline

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "backend" / "evals" / "datasets" / "fieldops-sop-golden-v1.json"


def test_normalize_handles_vietnamese_diacritics_and_codes():
    assert graders.normalize("CHƯA XÁC ĐỊNH") == "chua xac dinh"
    assert graders.normalize("Đội sở hữu") == "doi so huu"
    assert graders.fact_coverage("Giữ nguyên client_action_id.", [["client_action_id"]])[0] == 1.0


def test_fact_coverage_counts_groups_and_respects_word_boundaries():
    score, missing = graders.fact_coverage("Trạng thái ONLINE và OFFLINE", [["ONLINE"], ["OFFLINE"], ["UNKNOWN"]])
    assert score == pytest.approx(2 / 3) and missing == [["UNKNOWN"]]
    # "pon" không được khớp nhầm vào "phong"
    assert graders.fact_coverage("phòng máy", [["PON"]])[0] == 0.0
    assert graders.fact_coverage("anything", []) == (None, [])


def test_refusal_detection_is_specific():
    assert graders.is_refusal("Thông tin không có trong tài liệu đã upload.")
    assert graders.is_refusal("Tài liệu không cung cấp ngưỡng nhiệt độ cụ thể.")
    assert not graders.is_refusal("Không; UNKNOWN nghĩa là chưa đủ dữ liệu 【E1】.")


def test_forbidden_and_groundedness():
    assert graders.forbidden_hits("Mật khẩu mặc định là admin", ["mật khẩu mặc định là"]) == ["mật khẩu mặc định là"]
    context = ["Hồ sơ E-204 cần trạng thái LOS, trạng thái PON và ảnh màn hình mô phỏng."]
    assert graders.groundedness("Hồ sơ E-204 cần trạng thái LOS và PON 【E1】.", context) == 1.0
    assert graders.groundedness("Hãy đo công suất quang bằng máy OTDR ở bước sóng 1550nm.", context) == 0.0


def test_retrieval_scores_are_source_level():
    scores = graders.retrieval_scores(["a.txt", "a.txt", "b.txt", "c.txt"], ["b.txt"], k=5)
    assert scores["recall"] == 1.0 and scores["mrr"] == 0.5 and scores["hit"] == 1.0
    assert graders.retrieval_scores(["a.txt"], [], k=5) is None
    miss = graders.retrieval_scores(["a.txt"], ["z.txt"], k=5)
    assert miss["recall"] == 0.0 and miss["ndcg"] == 0.0


def test_gates_and_baseline_regression_direction():
    metrics = {"retrieval_mrr": 0.80, "safety_violations": 1, "false_refusal_rate": 0.05}
    failures = check_gates(metrics, {"min": {"retrieval_mrr": 0.85}, "max": {"safety_violations": 0}})
    assert len(failures) == 2
    regressions, deltas = compare_baseline(
        {"retrieval_mrr": 0.80, "false_refusal_rate": 0.10, "safety_violations": 0},
        {"retrieval_mrr": 0.90, "false_refusal_rate": 0.05, "safety_violations": 0},
    )
    assert any("retrieval_mrr" in r for r in regressions)
    assert any("false_refusal_rate" in r for r in regressions)
    assert deltas["safety_violations"] == 0


def test_golden_set_is_well_formed():
    data = load_dataset(DATASET)
    cases = data["cases"]
    assert len(cases) >= 60
    corpus = {p.name for p in (ROOT / data["corpus_dir"]).glob(data["corpus_glob"])}
    for case in cases:
        assert set(case["expected_sources"]) <= corpus, case["id"]
        if not case["expect_refusal"]:
            assert case["facts"], f"{case['id']} cần dữ kiện để chấm"
    assert sum(c["expect_refusal"] for c in cases) >= 10


def test_offline_suite_runs_end_to_end(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-B", str(ROOT / "backend" / "scripts" / "run_evals.py"), "--mode", "offline",
         "--limit", "12", "--output-dir", str(tmp_path)],
        capture_output=True, text=True, timeout=300, cwd=ROOT,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads(next(tmp_path.glob("*-offline-all.json")).read_text(encoding="utf-8"))
    assert report["metrics"]["cases"] == 12
    assert report["metrics"]["errors"] == 0
    assert report["metrics"]["retrieval_recall_at_k"] >= 0.9
    assert (tmp_path / "latest.md").read_text(encoding="utf-8").startswith("# FieldOps RAG eval")
    # Chi phí được đo bằng chính recorder của ứng dụng (offline = $0 nhưng có token).
    assert report["metrics"]["tokens_in"] > 0 and report["metrics"]["cost_usd"] == 0
