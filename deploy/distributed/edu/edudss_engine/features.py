"""Point-in-time weekly LMS features; missing observations remain missing."""
from __future__ import annotations

import numpy as np
import pandas as pd

REGRESSION_FEATURES = (
    "grade_mean_3w", "engagement_mean_3w", "engagement_acceleration",
    "observed_grade_weeks", "observed_engagement_weeks",
)
WEEK = pd.Timedelta(days=7)


def _utc(value):
    return pd.to_datetime(value, utc=True, errors="raise")


def _week_start(series):
    return series.dt.normalize() - pd.to_timedelta(series.dt.dayofweek, unit="D")


def _events(events):
    required = {"student_id", "course_id", "timestamp", "grade", "interaction_minutes"}
    if not isinstance(events, pd.DataFrame) or not required <= set(events):
        raise ValueError("Weekly events require student_id, course_id, timestamp, grade and interaction_minutes.")
    frame = events.copy()
    if frame.empty:
        raise ValueError("At least one LMS observation is required.")
    for column in ("student_id", "course_id"):
        if frame[column].isna().any() or frame[column].astype(str).str.strip().eq("").any():
            raise ValueError("Student and course identifiers must be explicit.")
        frame[column] = frame[column].astype(str)
    frame["timestamp"] = _utc(frame["timestamp"])
    frame["available_at"] = _utc(frame["available_at"]) if "available_at" in frame else frame["timestamp"]
    if frame[["timestamp", "available_at"]].isna().any().any():
        raise ValueError("Observation and availability timestamps must be explicit.")
    if (frame["available_at"] < frame["timestamp"]).any():
        raise ValueError("An observation cannot be available before it happened.")
    if frame.duplicated(["student_id", "course_id", "timestamp"]).any():
        raise ValueError("Duplicate student/course/timestamp observations must be reconciled.")
    for column in ("grade", "interaction_minutes"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
        if np.isinf(frame[column].to_numpy(dtype=float)).any():
            raise ValueError("Infinite measurements are invalid.")
    if ((frame["grade"].dropna() < 0) | (frame["grade"].dropna() > 10)).any():
        raise ValueError("Observed grades must use the documented 0..10 scale.")
    if (frame["interaction_minutes"].dropna() < 0).any():
        raise ValueError("Interaction minutes must be nonnegative.")
    frame["week"] = _week_start(frame["timestamp"])
    return frame.sort_values(["student_id", "course_id", "timestamp"], kind="stable")


def _past_features(group, window_end, known_at):
    start = window_end - 3 * WEEK
    history = group[
        (group["week"] >= start) & (group["week"] < window_end)
        & (group["available_at"] <= known_at)
    ]
    weeks = pd.date_range(start, periods=3, freq="7D", tz="UTC")
    grades = history.groupby("week")["grade"].mean().reindex(weeks)
    minutes = history.groupby("week")["interaction_minutes"].sum(min_count=1).reindex(weeks)
    acceleration = float(minutes.iloc[2] - 2 * minutes.iloc[1] + minutes.iloc[0]) if minutes.notna().all() else np.nan
    return {
        "grade_mean_3w": float(grades.mean()) if grades.notna().any() else np.nan,
        "engagement_mean_3w": float(minutes.mean()) if minutes.notna().any() else np.nan,
        "engagement_acceleration": acceleration,
        "observed_grade_weeks": int(grades.count()),
        "observed_engagement_weeks": int(minutes.count()),
        "feature_available_at": history["available_at"].max() if not history.empty else pd.NaT,
        "feature_window_end": window_end,
    }


def build_regression_dataset(events: pd.DataFrame) -> pd.DataFrame:
    """At each week start, predict that week's grade using prior completed weeks.

    A target becomes available no earlier than the end of its week. Explicit
    delayed available_at values are retained for validation-fold label purging.
    Weekly grades are observed means, interaction minutes observed sums.
    """
    frame = _events(events)
    records = []
    for (student, course), group in frame.groupby(["student_id", "course_id"], sort=True):
        first = group["week"].min() + 3 * WEEK
        for timestamp in pd.date_range(first, group["week"].max(), freq="7D", tz="UTC"):
            outcome = group[(group["week"] == timestamp) & group["grade"].notna()]
            if outcome.empty:
                continue
            records.append({
                "student_id": student, "course_id": course, "timestamp": timestamp,
                **_past_features(group, timestamp, timestamp),
                "target": float(outcome["grade"].mean()),
                "label_available_at": max(timestamp + WEEK, outcome["available_at"].max()),
            })
    columns = ["student_id", "course_id", "timestamp", *REGRESSION_FEATURES,
               "feature_available_at", "feature_window_end", "target", "label_available_at"]
    return pd.DataFrame.from_records(records, columns=columns).sort_values(
        ["timestamp", "student_id", "course_id"], kind="stable").reset_index(drop=True)


def build_prediction_features(events: pd.DataFrame, as_of) -> pd.DataFrame:
    """Create a target-free forecast using only fully completed known weeks.

    timestamp is the first Monday at or after as_of; feature_window_end is
    that target-week boundary. In-progress weeks remain missing, so the lag
    window stays aligned with training even between Mondays.
    """
    frame = _events(events)
    known_at = _utc(as_of)
    if pd.isna(known_at):
        raise ValueError("An explicit forecast as_of timestamp is required.")
    window_end = known_at.normalize() - pd.Timedelta(days=known_at.dayofweek)
    forecast = window_end if known_at == window_end else window_end + WEEK
    records = []
    for (student, course), group in frame.groupby(["student_id", "course_id"], sort=True):
        known = group[(group["week"] + WEEK <= known_at) & (group["available_at"] <= known_at)]
        if known.empty:
            continue
        records.append({
            "student_id": student, "course_id": course, "timestamp": forecast,
            **_past_features(known, forecast, known_at),
            "forecast_as_of": known_at,
            "forecast_horizon_days": float((forecast - known_at) / pd.Timedelta(days=1)),
        })
    return pd.DataFrame.from_records(records)
