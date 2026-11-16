"""Reproducible candidate validation; metrics are empirical, not accuracy proofs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.base import clone
from sklearn.decomposition import PCA
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Lasso, LinearRegression, LogisticRegression, Ridge
from sklearn.metrics import confusion_matrix, f1_score, mean_absolute_error, r2_score, recall_score
from sklearn.model_selection import StratifiedKFold, TimeSeriesSplit, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from .features import REGRESSION_FEATURES

FORBIDDEN_FEATURES = {
    "student_id", "course_id", "timestamp", "label_available_at", "feature_available_at",
    "feature_window_end", "forecast_as_of", "target", "risk_label", "future_grade",
    "final_grade", "outcome", "label",
}


@dataclass
class TrainingResult:
    model: Any
    leaderboard: list[dict]
    holdout: dict
    metadata: dict
    model_name: str

    def to_dict(self):
        return {"model_name": self.model_name, "leaderboard": self.leaderboard,
                "holdout": self.holdout, "metadata": self.metadata}


def _iso(value):
    return pd.Timestamp(value).isoformat()


def _std(values):
    return float(np.std(values, ddof=1))


def _features(frame, columns):
    columns = list(columns)
    if not columns or len(columns) != len(set(columns)):
        raise ValueError("A nonempty, unique feature allowlist is required.")
    if any(name in FORBIDDEN_FEATURES or str(name).endswith(("_id", "_label")) for name in columns):
        raise ValueError("Targets, identifiers and timestamps cannot be predictors.")
    if not set(columns) <= set(frame):
        raise ValueError("A requested feature column is missing.")
    result = frame[columns].apply(pd.to_numeric, errors="raise").astype(float)
    if np.isinf(result.to_numpy()).any():
        raise ValueError("Infinite predictors are invalid; missing predictors may be imputed.")
    return result


def _frame(frame, target, columns, as_of):
    required = {"student_id", "timestamp", "label_available_at", target}
    if not isinstance(frame, pd.DataFrame) or not required <= set(frame):
        raise ValueError("Training needs identifiers, snapshot/label availability timestamps and the target.")
    data = frame.copy()
    for column in ("timestamp", "label_available_at"):
        data[column] = pd.to_datetime(data[column], utc=True, errors="raise")
        if data[column].isna().any():
            raise ValueError("Missing validation timestamps are invalid.")
    if data["student_id"].isna().any():
        raise ValueError("Student identifiers are required for leakage audits.")
    if (data["label_available_at"] <= data["timestamp"]).any():
        raise ValueError("Prediction outcomes must become available after the feature snapshot.")
    if "feature_available_at" in data:
        availability = pd.to_datetime(data["feature_available_at"], utc=True, errors="raise")
        if (availability > data["timestamp"]).any():
            raise ValueError("A feature uses information unavailable at its prediction timestamp.")
    data[target] = pd.to_numeric(data[target], errors="raise")
    if data[target].isna().any() or np.isinf(data[target].to_numpy(dtype=float)).any():
        raise ValueError("Training targets must be observed finite measurements.")
    inferred = as_of is None
    cutoff = data["label_available_at"].max() if inferred else pd.to_datetime(as_of, utc=True, errors="raise")
    if pd.isna(cutoff):
        raise ValueError("An explicit valid training as_of is required.")
    data = data[data["label_available_at"] <= cutoff].sort_values(
        ["timestamp", "student_id"], kind="stable").reset_index(drop=True)
    if data.empty:
        raise ValueError("No target outcomes were available at the training cutoff.")
    _features(data, columns)
    return data, cutoff, inferred


def temporal_folds(frame: pd.DataFrame, n_splits=5):
    """Split unique timestamps, then purge training labels unavailable at validation start."""
    if not isinstance(n_splits, int) or isinstance(n_splits, bool) or n_splits < 2:
        raise ValueError("At least two temporal folds are required.")
    timestamps = pd.to_datetime(frame["timestamp"], utc=True, errors="raise")
    available = pd.to_datetime(frame["label_available_at"], utc=True, errors="raise")
    unique = pd.DatetimeIndex(timestamps.unique()).sort_values()
    if len(unique) < n_splits + 1:
        raise ValueError("Not enough independent timestamp groups for temporal CV.")
    result = []
    for past, future in TimeSeriesSplit(n_splits=n_splits).split(unique):
        start = unique[future[0]]
        train = np.flatnonzero(timestamps.isin(unique[past]) & (available <= start))
        validation = np.flatnonzero(timestamps.isin(unique[future]))
        if len(train) < 2 or len(validation) < 2:
            raise ValueError("Each purged temporal fold needs at least two training and validation rows.")
        result.append((train, validation))
    return result


def _preprocess(estimator, *, pca_components=None):
    steps = [
        ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
        ("scaler", StandardScaler()),
    ]
    if pca_components is not None:
        steps.append(("pca", PCA(n_components=pca_components, svd_solver="full")))
    return Pipeline([*steps, ("model", estimator)])


def regression_candidates(seed=42):
    return {
        "LinearRegression": _preprocess(LinearRegression()),
        "Lasso": _preprocess(Lasso(alpha=0.02, max_iter=5000)),
        "Ridge": _preprocess(Ridge(alpha=1.0)),
        "LightGBMRegressor": _preprocess(LGBMRegressor(
            n_estimators=100, num_leaves=15, learning_rate=0.05, random_state=seed,
            n_jobs=1, verbosity=-1, force_col_wise=True)),
    }


def _regression_metrics(actual, prediction):
    return {"mae": float(mean_absolute_error(actual, prediction)),
            "r2": float(r2_score(actual, prediction))}


def train_regression(frame, feature_columns=REGRESSION_FEATURES, *, n_splits=5,
                     holdout_fraction=0.2, seed=42, mae_tolerance=1e-6,
                     as_of=None, refit_all=True):
    """Evaluate past->future candidate pipelines, then optionally refit known data.

    Holdout metrics always describe the development-only model, before the
    optional serving refit. A new serving refit has no separate holdout score.
    """
    if not 0 < holdout_fraction < 0.5 or mae_tolerance < 0:
        raise ValueError("Invalid holdout fraction or MAE tie tolerance.")
    data, cutoff, inferred = _frame(frame, "target", feature_columns, as_of)
    if not data["target"].between(0, 10).all():
        raise ValueError("Regression targets must use the documented grade scale 0..10.")
    times = pd.DatetimeIndex(data["timestamp"].unique()).sort_values()
    holdout_groups = max(1, int(np.ceil(len(times) * holdout_fraction)))
    if len(times) - holdout_groups < n_splits + 1:
        raise ValueError("More weekly timestamp groups are needed for 5-fold CV plus untouched holdout.")
    holdout_start = times[-holdout_groups]
    development = data[(data["timestamp"] < holdout_start)
                       & (data["label_available_at"] <= holdout_start)].reset_index(drop=True)
    holdout = data[data["timestamp"] >= holdout_start].reset_index(drop=True)
    if len(holdout) < 2:
        raise ValueError("The temporal holdout needs at least two observed targets.")
    folds = temporal_folds(development, n_splits)
    x, y = _features(development, feature_columns), development["target"]
    candidates = regression_candidates(seed)
    baseline = _preprocess(DummyRegressor(strategy="mean"))
    scores, audit = [], []
    for train, validation in folds:
        audit.append({
            "train_start": _iso(development.iloc[train]["timestamp"].min()),
            "train_end": _iso(development.iloc[train]["timestamp"].max()),
            "training_labels_available_max": _iso(development.iloc[train]["label_available_at"].max()),
            "validation_start": _iso(development.iloc[validation]["timestamp"].min()),
            "validation_end": _iso(development.iloc[validation]["timestamp"].max()),
            "train_rows": len(train), "validation_rows": len(validation),
        })
    for name, pipeline in {**candidates, "DummyMean": baseline}.items():
        fold_metrics = []
        for train, validation in folds:
            model = clone(pipeline).fit(x.iloc[train], y.iloc[train])
            fold_metrics.append(_regression_metrics(y.iloc[validation], model.predict(x.iloc[validation])))
        scores.append({
            "model": name, "mae_mean": float(np.mean([item["mae"] for item in fold_metrics])),
            "mae_std": _std([item["mae"] for item in fold_metrics]),
            "r2_mean": float(np.mean([item["r2"] for item in fold_metrics])),
            "r2_std": _std([item["r2"] for item in fold_metrics]), "folds": fold_metrics,
            "candidate": name in candidates,
        })
    eligible = [row for row in scores if row["candidate"]]
    best_mae = min(row["mae_mean"] for row in eligible)
    winner = min((row for row in eligible if row["mae_mean"] <= best_mae + mae_tolerance),
                 key=lambda row: (row["r2_std"], row["mae_mean"], row["model"]))["model"]
    scores.sort(key=lambda row: (row["mae_mean"], row["r2_std"], row["model"]))
    for row in scores:
        row["selected"] = row["model"] == winner
    selected = clone(candidates[winner]).fit(x, y)
    holdout_x = _features(holdout, feature_columns)
    metrics = _regression_metrics(holdout["target"], selected.predict(holdout_x))
    baseline_metrics = _regression_metrics(
        holdout["target"], clone(baseline).fit(x, y).predict(holdout_x))
    metrics.update(baseline=baseline_metrics, beats_baseline=metrics["mae"] < baseline_metrics["mae"],
                   rows=len(holdout), start=_iso(holdout_start), end=_iso(holdout["timestamp"].max()))
    serving = clone(candidates[winner]).fit(_features(data, feature_columns), data["target"]) if refit_all else selected
    fitted_data = data if refit_all else development
    metadata = dict(
        task="regression", winner=winner, n_splits=n_splits, std_ddof=1, seed=seed,
        feature_columns=list(feature_columns), folds=audit, as_of=_iso(cutoff),
        as_of_inferred=inferred, holdout_kind="last_timestamp_groups",
        selection_rule="minimum MAE; R2 STD breaks ties within mae_tolerance",
        mae_tolerance=mae_tolerance, holdout_evaluation_fit="development_only",
        serving_fit="all_known_label_rows" if refit_all else "development_only",
        trained_through=_iso(fitted_data["timestamp"].max()),
        training_labels_available_max=_iso(fitted_data["label_available_at"].max()),
        limitations="CV STD is descriptive; holdout performance does not prove future accuracy.",
    )
    return TrainingResult(serving, scores, metrics, metadata, winner)


def classification_candidates(pca_components, seed=42):
    return {
        "LogisticRegression": _preprocess(LogisticRegression(
            max_iter=1500, class_weight="balanced", random_state=seed), pca_components=pca_components),
        "RandomForestClassifier": _preprocess(RandomForestClassifier(
            n_estimators=120, max_depth=6, min_samples_leaf=2, class_weight="balanced",
            random_state=seed, n_jobs=1), pca_components=pca_components),
        "LightGBMClassifier": _preprocess(LGBMClassifier(
            n_estimators=100, num_leaves=15, learning_rate=0.05, random_state=seed,
            n_jobs=1, verbosity=-1, force_col_wise=True), pca_components=pca_components),
    }


def train_classification(frame, feature_columns=None, *, n_splits=5,
                         holdout_fraction=0.2, seed=42, f1_tolerance=1e-6,
                         as_of=None, refit_all=True):
    """Predict three risk classes for distinct students using fold-local PCA.

    Stratified sampling evaluates generalization to other students, not future
    time periods. Repeated student snapshots must be grouped separately rather
    than leaking the same learner into train and validation.
    """
    if not isinstance(n_splits, int) or isinstance(n_splits, bool) or n_splits < 2:
        raise ValueError("At least two classification folds are required.")
    if not 0 < holdout_fraction < 0.5 or f1_tolerance < 0:
        raise ValueError("Invalid holdout fraction or F1 tie tolerance.")
    columns = list(feature_columns) if feature_columns is not None else [
        name for name in frame if str(name).startswith("interaction_")]
    data, cutoff, inferred = _frame(frame, "risk_label", columns, as_of)
    if data["student_id"].astype(str).duplicated().any():
        raise ValueError("Classification requires one snapshot per student; use grouped validation for repeated learners.")
    if set(data["risk_label"].unique()) != {0, 1, 2}:
        raise ValueError("Risk labels must contain exactly the three classes 0, 1 and 2.")
    data["risk_label"] = data["risk_label"].astype(int)
    positions = np.arange(len(data))
    try:
        development_positions, holdout_positions = train_test_split(
            positions, test_size=holdout_fraction, stratify=data["risk_label"],
            random_state=seed,
        )
    except ValueError as error:
        raise ValueError("Enough students in every class are needed for stratified holdout.") from error
    development = data.iloc[development_positions].reset_index(drop=True)
    holdout = data.iloc[holdout_positions].reset_index(drop=True)
    if development["risk_label"].value_counts().min() < n_splits:
        raise ValueError("Every development risk class needs at least n_splits students after holdout.")
    x, y = _features(development, columns), development["risk_label"]
    folds = list(StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed).split(x, y))
    components = min(20, len(columns), min(len(train) - 1 for train, _ in folds))
    if components < 1:
        raise ValueError("Not enough training rows for PCA.")
    candidates = classification_candidates(components, seed)
    baseline = _preprocess(DummyClassifier(strategy="most_frequent"), pca_components=components)
    scores = []
    for name, pipeline in {**candidates, "DummyMajority": baseline}.items():
        values = []
        for train, validation in folds:
            model = clone(pipeline).fit(x.iloc[train], y.iloc[train])
            values.append(float(f1_score(
                y.iloc[validation], model.predict(x.iloc[validation]),
                labels=[0, 1, 2], average="macro", zero_division=0)))
        scores.append({
            "model": name, "macro_f1_mean": float(np.mean(values)),
            "macro_f1_std": _std(values), "folds": values,
            "candidate": name in candidates,
        })
    eligible = [row for row in scores if row["candidate"]]
    best_f1 = max(row["macro_f1_mean"] for row in eligible)
    winner = min((row for row in eligible if row["macro_f1_mean"] >= best_f1 - f1_tolerance),
                 key=lambda row: (row["macro_f1_std"], -row["macro_f1_mean"], row["model"]))["model"]
    scores.sort(key=lambda row: (-row["macro_f1_mean"], row["macro_f1_std"], row["model"]))
    for row in scores:
        row["selected"] = row["model"] == winner
    selected = clone(candidates[winner]).fit(x, y)
    predictions = selected.predict(_features(holdout, columns))
    baseline_predictions = clone(baseline).fit(x, y).predict(_features(holdout, columns))
    macro_f1 = float(f1_score(holdout["risk_label"], predictions, labels=[0, 1, 2],
                             average="macro", zero_division=0))
    baseline_f1 = float(f1_score(holdout["risk_label"], baseline_predictions, labels=[0, 1, 2],
                                average="macro", zero_division=0))
    metrics = dict(
        macro_f1=macro_f1, baseline_macro_f1=baseline_f1, beats_baseline=macro_f1 > baseline_f1,
        rows=len(holdout), confusion_matrix=confusion_matrix(
            holdout["risk_label"], predictions, labels=[0, 1, 2]).tolist(),
        per_class_recall=recall_score(
            holdout["risk_label"], predictions, labels=[0, 1, 2],
            average=None, zero_division=0).tolist(),
        class_labels=[0, 1, 2],
    )
    serving = clone(candidates[winner]).fit(_features(data, columns), data["risk_label"]) if refit_all else selected
    fitted_data = data if refit_all else development
    audit = []
    for train, validation in folds:
        audit.append({
            "train_rows": len(train), "validation_rows": len(validation),
            "train_class_counts": {str(k): int(v) for k, v in y.iloc[train].value_counts().items()},
            "validation_class_counts": {str(k): int(v) for k, v in y.iloc[validation].value_counts().items()},
            "student_overlap": len(set(development.iloc[train]["student_id"])
                                   & set(development.iloc[validation]["student_id"])),
        })
    metadata = dict(
        task="classification", winner=winner, n_splits=n_splits, std_ddof=1, seed=seed,
        feature_columns=columns, pca_components=components, folds=audit,
        as_of=_iso(cutoff), as_of_inferred=inferred, holdout_kind="stratified_distinct_student",
        selection_rule="maximum Macro F1; STD breaks ties within f1_tolerance",
        f1_tolerance=f1_tolerance, holdout_evaluation_fit="development_only",
        serving_fit="all_known_label_rows" if refit_all else "development_only",
        trained_through=_iso(fitted_data["timestamp"].max()),
        training_labels_available_max=_iso(fitted_data["label_available_at"].max()),
        limitations="Stratified CV measures distinct-student generalization, not future-time performance.",
    )
    return TrainingResult(serving, scores, metrics, metadata, winner)
