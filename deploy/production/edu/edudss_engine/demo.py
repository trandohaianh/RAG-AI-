"""Run all three local ML paths on labelled synthetic data without Redis or LLM."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys

from .demo_data import COURSE_ID, GRAPH_EDGES, SCHOOL_ID, make_demo


def write_demo_env(path):
    """Create private demo credentials once; never print or overwrite secrets."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    values = {
        "REDIS_PASSWORD": secrets.token_urlsafe(32),
        "EDUDSS_ENGINE_SIGNING_KEY": secrets.token_hex(32),
        "EDUDSS_ENGINE_DEMO": "1",
        "EDUDSS_ENGINE_LLM_BASE_URL": "",
        "EDUDSS_ENGINE_LLM_MODEL": "demo",
        "EDUDSS_ENGINE_LLM_API_KEY": "",
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("# Generated demo credentials; keep this file private.\n")
        stream.write("\n".join(f"{name}={value}" for name, value in values.items()) + "\n")
    return path.resolve()


def run_demo(*, seed=42, n_students=24, n_weeks=24, n_classification=120):
    import pandas as pd

    from .features import REGRESSION_FEATURES, build_prediction_features, build_regression_dataset
    from .recommendation import HybridRecommender, evaluate_temporal
    from .validation import train_classification, train_regression

    data = make_demo(seed, n_students, n_weeks, n_classification)
    as_of = pd.Timestamp(data["as_of"])
    regression = train_regression(build_regression_dataset(pd.DataFrame(data["events"])),
                                  as_of=as_of)
    classification = train_classification(pd.DataFrame(data["classification"]), as_of=as_of)
    model = HybridRecommender(random_state=seed).fit(
        pd.DataFrame(data["items"]), pd.DataFrame(data["ratings"]), GRAPH_EDGES,
        school_id=SCHOOL_ID, course_id=COURSE_ID, cutoff=as_of,
    )
    profile = data["profiles"]["S000"]
    recommendations = model.recommend(
        "S000", k=5, mastery=profile["mastery"], weak_topics=profile["weak_topics"],
        interests=profile["interests"],
    )
    forecast = build_prediction_features(pd.DataFrame(data["events"]), as_of)
    own = forecast[forecast.student_id == "S000"].iloc[[0]]
    grade = float(regression.model.predict(own[list(REGRESSION_FEATURES)])[0])
    interactions = pd.DataFrame([profile["interaction_features"]])[
        classification.metadata["feature_columns"]]
    risk = int(classification.model.predict(interactions)[0])
    probabilities = classification.model.predict_proba(interactions)[0]
    cutoff = as_of - pd.Timedelta(weeks=6)
    # No final-as_of mastery or weak-topic evidence is passed back into the
    # earlier evaluation. A fixed public curriculum interest is known in advance.
    public_interests = {student: "numbers arithmetic foundational practice"
                        for student in data["profiles"]}
    recommendation_evaluation = evaluate_temporal(
        pd.DataFrame(data["items"]), pd.DataFrame(data["ratings"]), GRAPH_EDGES,
        school_id=SCHOOL_ID, course_id=COURSE_ID, cutoff=cutoff, k=5,
        interests_by_student=public_interests,
        recommender_kwargs={"random_state": seed},
    )
    return {
        "source": "synthetic",
        "notice": (
            "SYNTHETIC DEMO: metrics describe simulated observations, not a real school, "
            "production latency, educational impact or guaranteed future accuracy."
        ),
        "as_of": data["as_of"], "seed": seed,
        "dataset": {
            "weekly_events": len(data["events"]), "serving_students": n_students,
            "classification_students": len(data["classification"]),
            "classification_cohort": "balanced synthetic outcomes, not real risk prevalence",
            "items": len(data["items"]), "ratings": len(data["ratings"]),
            "serving_ids_disjoint_from_classification_training": True,
        },
        "regression": regression.to_dict(),
        "classification": classification.to_dict(),
        "student_example": {
            "student_id": "S000", "course_id": COURSE_ID,
            "predicted_grade": grade, "predicted_risk_class": risk,
            "risk_class_probabilities_uncalibrated": [float(value) for value in probabilities],
            "feature_available_at": own.iloc[0]["feature_available_at"].isoformat(),
            "forecast_timestamp": own.iloc[0]["timestamp"].isoformat(),
            "weak_topics_source": "synthetic observed diagnostic profile, not aggregate grade inference",
            "recommendations": recommendations,
        },
        "recommendation_evaluation": recommendation_evaluation,
        "recommendation_evidence_boundary": (
            "Earlier-cutoff evaluation receives no final-date student mastery or interests. "
            "Only fixed public curriculum interests are supplied; unknown prerequisites stay blocked."
        ),
    }


def _print_report(report):
    import pandas as pd

    print(report["notice"])
    print("\nREGRESSION: five timestamp-group folds; untouched final-time holdout")
    print(pd.DataFrame(report["regression"]["leaderboard"])[
        ["model", "mae_mean", "mae_std", "r2_mean", "r2_std", "selected"]
    ].to_string(index=False))
    print("Holdout:", json.dumps(report["regression"]["holdout"], ensure_ascii=True))
    print("\nCLASSIFICATION: five stratified distinct-student folds; untouched holdout")
    print(pd.DataFrame(report["classification"]["leaderboard"])[
        ["model", "macro_f1_mean", "macro_f1_std", "selected"]
    ].to_string(index=False))
    print("Holdout:", json.dumps(report["classification"]["holdout"], ensure_ascii=True))
    print("\nS000 RECOMMENDATIONS: unmet prerequisite topics first")
    for item in report["student_example"]["recommendations"]:
        print(json.dumps(item, ensure_ascii=True))
    print("\nTEMPORAL RECOMMENDATION EVALUATION")
    print(json.dumps(report["recommendation_evaluation"], ensure_ascii=True, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write measured demo results as JSON.")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--students", type=int, default=24)
    parser.add_argument("--weeks", type=int, default=24)
    parser.add_argument("--classification-students", type=int, default=120)
    parser.add_argument("--write-env", type=Path, help="Create a new private .env.demo; refuses overwrite.")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        if args.write_env:
            path = write_demo_env(args.write_env)
            print("Created private demo environment: " + str(path))
        report = run_demo(seed=args.seed, n_students=args.students, n_weeks=args.weeks,
                          n_classification=args.classification_students)
        _print_report(report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Saved report: " + str(args.output.resolve()))
        return 0
    except FileExistsError:
        print("Refused to overwrite an existing environment file.", file=sys.stderr)
        return 2
    except (ValueError, RuntimeError) as error:
        print("Demo failed: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
