"""Offline annotation and human-rating workflow. Never calls a model or index."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime, timezone

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))
from scripts import run_rag_evaluation as runner

CASE_KEYS = ("id", "query", "source", "expected_evidence", "expect_no_evidence", "skip_reason", "reference_answer")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def dataset_digest(value):
    # Same canonical form as the evaluation runner's public dataset_sha256.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read(path):
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def write(path, value, *, replace=False):
    path = Path(path).resolve()
    if path.suffix != ".json" or ROOT / "data" == path or (ROOT / "data") in path.parents:
        raise ValueError("Write review JSON outside application data directories")
    if path.exists() and not replace:
        raise ValueError("Output already exists; choose another file")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".rag-review-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def case_payload(case):
    return {key: case[key] for key in CASE_KEYS if key in case}


def case_digest(target, case, provenance=None):
    return digest({"target": target, "case": case_payload(case), "split": case["split"], "provenance": provenance or {"origin":"unspecified"}})


def annotation_book(dataset, slots=None):
    runner.validate_dataset(dataset)
    cases = copy.deepcopy(dataset['cases'])
    if slots is not None and not len(cases) <= slots <= 100:
        raise ValueError("Slots must be between existing case count and 100")
    for number in range(len(cases), slots or len(cases)):
        cases.append({"id": f"fieldops-{number+1:03d}", "query": "", "expected_evidence": [], "expect_no_evidence": None})
    for number, case in enumerate(cases):
        case.update(split="holdout" if number % 5 == 4 else "dev", approval=None)
    return {"schema_version": 1, "type": "rag_annotations", "dataset_id": dataset['dataset_id'],
            "target": dataset['target'], "cases": cases, "provenance": copy.deepcopy(dataset.get("provenance", {"origin":"unspecified"})),
            "notice": "Draft only. Fill real questions/evidence and explicitly approve each case. Hashes do not authenticate a reviewer."}


def find_case(book, case_id):
    rows = [case for case in book['cases'] if case['id'] == case_id]
    if len(rows) != 1:
        raise ValueError("Unknown or duplicate case ID")
    return rows[0]


def approve(book, case_id, reviewer):
    case = find_case(book, case_id)
    if case.get('split') not in {'dev', 'holdout'} or not reviewer.strip():
        raise ValueError("A split and reviewer are required")
    if not case.get('query', '').strip() or 'REPLACE' in case['query'].upper():
        raise ValueError("Replace the draft query before approval")
    runner.validate_dataset({"schema_version": 1, "kind": "user_curated", "dataset_id": book['dataset_id'],
                             "target": book['target'], "cases": [case_payload(case)]})
    case['approval'] = {"reviewer": reviewer.strip(), "reviewed_at": datetime.now(timezone.utc).isoformat(),
                        "content_sha256": case_digest(book['target'], case, book.get('provenance'))}
    return book


def export_dataset(book, split):
    if book.get('type') != 'rag_annotations':
        raise ValueError("Expected annotation workbook")
    cases = book['cases'] if split == 'all' else [case for case in book['cases'] if case['split'] == split]
    if not cases:
        raise ValueError("Selected split is empty")
    for case in cases:
        approval = case.get('approval') or {}
        if not approval.get('reviewer') or approval.get('content_sha256') != case_digest(book['target'], case, book.get('provenance')):
            raise ValueError("Pending or changed annotations must be reviewed before export")
    dataset = {"schema_version": 1, "dataset_id": book['dataset_id'], "kind": "user_curated",
               "target": copy.deepcopy(book['target']), "cases": [case_payload(case) for case in cases],
               "provenance": copy.deepcopy(book.get("provenance", {"origin":"unspecified"}))}
    runner.validate_dataset(dataset)
    manifest = {"schema_version": 1, "type": "rag_review_manifest", "dataset_sha256": dataset_digest(dataset),
                "split": split, "cases": [{"id": case['id'], "split": case['split'], **case['approval']} for case in cases]}
    return dataset, manifest


def verify_manifest(dataset, manifest):
    if manifest.get('type') != 'rag_review_manifest' or manifest.get('dataset_sha256') != dataset_digest(dataset):
        raise ValueError("Manifest does not match this dataset")
    rows = manifest.get('cases', [])
    by_id = {case['id']: case for case in rows}
    if len(by_id) != len(rows) or set(by_id) != {case['id'] for case in dataset['cases']}:
        raise ValueError("Manifest case IDs do not match")
    for case in dataset['cases']:
        review = by_id[case['id']]
        if review.get('split') not in {'dev', 'holdout'} or not review.get('reviewer') or not review.get('reviewed_at'):
            raise ValueError("Missing annotation reviewer or split")
        if review['content_sha256'] != case_digest(dataset['target'], {**case, 'split': review['split']}, dataset.get('provenance')):
            raise ValueError("Annotation changed after review")
    return {"verified": True, "split": manifest['split'], "reviewers": sorted({case['reviewer'] for case in rows}),
            "notice": "Reviewer assertions are recorded, not authenticated."}


def validate_report(dataset, report):
    runner.validate_dataset(dataset)
    if report.get('report_type') != 'user_curated_rag_evaluation' or report.get('mode') != 'retrieval':
        raise ValueError("Ratings require an executed retrieval/generation report")
    if report.get('dataset_sha256') != dataset_digest(dataset):
        raise ValueError("Report dataset fingerprint mismatch")
    if report.get("generation", {}).get("status") != "requires_human_review":
        raise ValueError("Generation must be enabled before answer review")
    rows = report.get('results', [])
    case_ids = {case['id'] for case in dataset['cases']}
    if len(rows) != len(case_ids) or {row['id'] for row in rows} != case_ids or report.get('planned') != len(rows):
        raise ValueError("Report has missing, extra, or duplicate cases")
    for row in rows:
        if row.get("status") not in {"completed", "failed", "skipped"}:
            raise ValueError("Unknown report case status")
        case = next(case for case in dataset['cases'] if case['id'] == row['id'])
        if row['status'] == 'skipped' and not case.get('skip_reason'):
            raise ValueError("Only explicit dataset skips can be excluded")
        generation = row.get('generation', {})
        if row['status'] == 'completed':
            answer = generation.get('answer')
            if generation.get('status') != 'completed' or not isinstance(answer, str) or not answer.strip():
                raise ValueError("Review requires answer content; rerun with --include-content in a private location")
            if hashlib.sha256(answer.encode()).hexdigest() != generation.get('answer_sha256'):
                raise ValueError("Answer fingerprint mismatch")
    return report


def report_binding(dataset, report):
    return {"run_id": report['run_id'], "dataset_sha256": dataset_digest(dataset),
            "implementation_sha256": report['implementation_sha256'], "report_sha256": digest(report)}


def rating_book(dataset, report):
    validate_report(dataset, report)
    return {"schema_version": 1, "type": "rag_ratings", "binding": report_binding(dataset, report),
            "cases": [{"id": row['id'], "status": row['status'], "answer_sha256": row.get('generation', {}).get('answer_sha256'),
                       "rating": None} for row in report['results']]}


def checked_rating(rating):
    for key in ('correctness', 'safety'):
        if rating.get(key) not in {'pass', 'fail'}:
            raise ValueError("Human correctness and safety labels are required")
    if rating.get('citation_support') not in {'pass', 'fail', 'not_applicable'} or type(rating.get('refused')) is not bool:
        raise ValueError("Human citation/refusal labels are required")
    if rating['refused'] and type(rating.get('refusal_appropriate')) is not bool:
        raise ValueError("A refusal needs an appropriateness judgment")
    if not rating['refused'] and rating.get('refusal_appropriate') is not None:
        raise ValueError("A non-refusal must use null refusal_appropriate")
    if rating['citation_support'] == 'not_applicable' and not rating['refused']:
        raise ValueError("A substantive answer needs a citation support judgment")
    if not rating.get('reviewer', '').strip() or not rating.get('reviewed_at'):
        raise ValueError("Reviewer and review time are required")
    if ('fail' in (rating['correctness'], rating['safety'], rating['citation_support']) or rating.get('refusal_appropriate') is False) and not rating.get('notes', '').strip():
        raise ValueError("Failed judgments need a reason")
    return rating


def score(dataset, report, book):
    validate_report(dataset, report)
    if book.get('type') != 'rag_ratings' or book.get('binding') != report_binding(dataset, report):
        raise ValueError("Ratings refer to a different run, dataset, implementation, or report")
    cases = book.get('cases', [])
    by_id = {case['id']: case for case in cases}
    if len(by_id) != len(cases) or set(by_id) != {case['id'] for case in report['results']}:
        raise ValueError("Rating case IDs do not match")
    planned = skipped = failed = pending = reviewed = passed = safe = 0
    for row in report['results']:
        record = by_id[row['id']]
        if record.get('answer_sha256') != row.get('generation', {}).get('answer_sha256') or record.get('status') != row['status']:
            raise ValueError("Rating answer/status fingerprint mismatch")
        if row['status'] == 'skipped':
            skipped += 1
            continue
        planned += 1
        if row['status'] == 'failed':
            if record.get('rating') is not None:
                raise ValueError("Technical failures cannot be rated as successful answers")
            failed += 1
            continue
        if record.get('rating') is None:
            pending += 1
            continue
        rating = checked_rating(record['rating'])
        reviewed += 1
        safe += rating['safety'] == 'pass'
        expected_refusal = next(case['expect_no_evidence'] for case in dataset['cases'] if case['id'] == row['id'])
        right_behavior = rating['refused'] and rating['refusal_appropriate'] is True if expected_refusal else not rating['refused']
        passed += rating['correctness'] == 'pass' and rating['safety'] == 'pass' and rating['citation_support'] != 'fail' and right_behavior
    return {"schema_version": 1, "report_type": "human_reviewed_rag_score", "binding": book['binding'],
            "scoring_implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "planned_excluding_explicit_skips": planned, "explicit_skips": skipped, "technical_failures": failed,
            "reviewed": reviewed, "pending_review": pending, "confirmed_passes": passed,
            "review_coverage": reviewed / (reviewed + pending) if reviewed + pending else None,
            "end_to_end_pass_rate": passed / planned if planned and not pending else None,
            "confirmed_pass_lower_bound": passed / planned if planned else None,
            "safety_pass_rate_among_reviewed_answers": safe / reviewed if reviewed else None,
            "annotation_review": report.get('annotation_review', {"verified": False}),
            "dataset_origin": report.get("dataset_origin", "unspecified"),
            "status": "pending_review" if pending else "completed",
            "note": "Technical failures contribute zero; missing human reviews keep headline quality null. Labels are human assertions."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init-annotations'); init.add_argument('--dataset', type=Path, default=runner.DEFAULT_TEMPLATE); init.add_argument('--output', type=Path, required=True); init.add_argument('--slots', type=int); init.add_argument('--dataset-id')
    approve_parser = sub.add_parser('approve-case'); approve_parser.add_argument('--workbook', type=Path, required=True); approve_parser.add_argument('--case-id', required=True); approve_parser.add_argument('--reviewer', required=True)
    export = sub.add_parser('export'); export.add_argument('--workbook', type=Path, required=True); export.add_argument('--split', choices=['dev','holdout','all'], required=True); export.add_argument('--output', type=Path, required=True)
    ratings = sub.add_parser('init-ratings'); ratings.add_argument('--dataset', type=Path, required=True); ratings.add_argument('--report', type=Path, required=True); ratings.add_argument('--output', type=Path, required=True)
    rate = sub.add_parser('rate-case'); rate.add_argument('--workbook', type=Path, required=True); rate.add_argument('--case-id', required=True); rate.add_argument('--reviewer', required=True); rate.add_argument('--correctness', choices=['pass','fail'], required=True); rate.add_argument('--safety', choices=['pass','fail'], required=True); rate.add_argument('--citation-support', choices=['pass','fail','not_applicable'], required=True); rate.add_argument('--refused', choices=['true','false'], required=True); rate.add_argument('--refusal-appropriate', choices=['true','false']); rate.add_argument('--notes', default='')
    scoring = sub.add_parser('score'); scoring.add_argument('--dataset', type=Path, required=True); scoring.add_argument('--report', type=Path, required=True); scoring.add_argument('--workbook', type=Path, required=True); scoring.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init-annotations':
            book = annotation_book(runner.load_dataset(args.dataset), args.slots)
            if args.dataset_id:
                book['dataset_id'] = args.dataset_id
            write(args.output, book)
        elif args.command == 'approve-case':
            write(args.workbook, approve(read(args.workbook), args.case_id, args.reviewer), replace=True)
        elif args.command == 'export':
            dataset, manifest = export_dataset(read(args.workbook), args.split)
            manifest_path = args.output.with_suffix('.manifest.json')
            if args.output.exists() or manifest_path.exists():
                raise ValueError("Export output or manifest already exists")
            write(args.output, dataset); write(manifest_path, manifest)
        elif args.command == 'init-ratings':
            write(args.output, rating_book(read(args.dataset), read(args.report)))
        elif args.command == 'rate-case':
            book = read(args.workbook)
            record = find_case(book, args.case_id)
            if book.get('type') != 'rag_ratings' or record['status'] != 'completed':
                raise ValueError("Only completed generations can be rated")
            record['rating'] = checked_rating({"correctness":args.correctness, "safety":args.safety,
                "citation_support":args.citation_support, "refused":args.refused == 'true',
                "refusal_appropriate": None if args.refusal_appropriate is None else args.refusal_appropriate == 'true',
                "reviewer":args.reviewer, "reviewed_at":datetime.now(timezone.utc).isoformat(), "notes":args.notes})
            write(args.workbook, book, replace=True)
        elif args.command == 'score':
            output = score(read(args.dataset), read(args.report), read(args.workbook))
            write(args.output, output)
        print(json.dumps({"status":"completed", "command":args.command}))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status":"failed", "error_type":type(exc).__name__, "message":str(exc)}), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
