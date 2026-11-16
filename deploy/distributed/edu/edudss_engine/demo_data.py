"""Deterministic synthetic cohorts for the standalone engine demonstration.

Outcomes are stochastic future measurements driven by persistent latent traits,
not copies of a predictor or deterministic risk labels derived from inputs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

GRAPH_EDGES = (
    ("numbers", "fractions"), ("fractions", "algebra"),
    ("algebra", "linear_equations"), ("numbers", "geometry"),
)
TOPICS = ("numbers", "fractions", "algebra", "linear_equations", "geometry")
INTERACTION_FEATURES = tuple(f"interaction_{index:02d}" for index in range(32))
SCHOOL_ID = "demo-school"
COURSE_ID = "MATH101"


def _iso(value):
    return pd.Timestamp(value).isoformat()


def _interactions(rng, ability, engagement):
    values = {}
    for index, name in enumerate(INTERACTION_FEATURES):
        kind = index % 4
        scale = (
            5 + 26 * engagement if kind == 0 else
            3 + 16 * engagement * ability if kind == 1 else
            2 + 15 * engagement * (1 - ability) if kind == 2 else
            4 + 18 * engagement
        )
        inactive = rng.random() < 0.2 + 0.4 * (1 - engagement)
        values[name] = 0.0 if inactive else float(rng.poisson(max(0.2, scale)))
    return values


def make_demo(seed=42, n_students=24, n_weeks=24, n_classification=120):
    """Produce JSON-ready fake weekly events, distinct-student risk and ratings.

    Classification is deliberately balanced for demonstrating five-fold CV;
    its artificial prevalence must never be interpreted as a school estimate.
    """
    if (type(n_students) is not int or n_students < 6 or type(n_weeks) is not int
            or n_weeks < 16 or type(n_classification) is not int or n_classification < 45):
        raise ValueError("Demo needs >=6 students, >=16 weeks and >=45 classification snapshots.")
    rng = np.random.default_rng(seed)
    origin = pd.Timestamp("2025-01-06", tz="UTC")
    as_of = origin + pd.Timedelta(weeks=n_weeks)
    events, profiles, traits = [], {}, {}
    for index in range(n_students):
        student = f"S{index:03d}"
        ability = 0.32 if index == 0 else float(rng.beta(3, 2))
        engagement = float(np.clip(0.25 + 0.65 * ability + rng.normal(0, 0.15), 0.05, 0.98))
        traits[student] = (ability, engagement)
        skill = ability
        for week in range(n_weeks):
            timestamp = origin + pd.Timedelta(weeks=week, days=6, hours=23)
            skill = float(np.clip(0.85 * skill + 0.15 * ability + rng.normal(0, 0.045), 0, 1))
            grade = float(np.clip(1.4 + 6.7 * skill + 0.025 * week + rng.normal(0, 0.85), 0, 10))
            minutes = float(max(0, 12 + 68 * engagement + 5 * np.sin(week / 2)
                                + rng.normal(0, 12)))
            events.append(dict(student_id=student, course_id=COURSE_ID,
                               timestamp=_iso(timestamp), available_at=_iso(timestamp),
                               grade=grade, interaction_minutes=minutes))
        mastery = {
            topic: float(np.clip(ability + offset + rng.normal(0, 0.08), 0, 1))
            for topic, offset in zip(TOPICS, (0.13, -0.03, -0.14, -0.22, -0.08))
        }
        if index == 0:
            mastery = dict(numbers=0.35, fractions=0.25, algebra=0.2,
                           linear_equations=0.12, geometry=0.3)
        profiles[student] = dict(
            observed_at=_iso(as_of - pd.Timedelta(minutes=30)),
            interaction_features=_interactions(rng, ability, engagement),
            mastery=mastery, weak_topics=["linear_equations"],
            interests="numbers arithmetic foundations examples before algebra linear equations",
        )

    # Balanced rejection sampling uses outcomes from an independent future exam
    # and dropout process. Feature vectors use only earlier latent engagement.
    classification = []
    for index in range(n_classification):
        requested = index % 3
        for _ in range(20000):
            ability = float(rng.beta(3, 2))
            engagement = float(np.clip(0.2 + 0.65 * ability + rng.normal(0, 0.16), 0.03, 0.99))
            final_exam = float(np.clip(1.2 + 7.5 * ability + rng.normal(0, 1.1), 0, 10))
            dropout_probability = 0.025 + 0.35 * (1 - engagement) ** 2
            dropout = bool(rng.random() < dropout_probability)
            label = 2 if dropout or final_exam < 4 else 1 if final_exam < 6.5 else 0
            if label == requested:
                break
        else:
            raise RuntimeError("Unable to generate the requested synthetic risk cohort.")
        classification.append(dict(
            student_id=f"C{index:03d}", course_id=COURSE_ID,
            timestamp=_iso(as_of - pd.Timedelta(weeks=6)),
            label_available_at=_iso(as_of - pd.Timedelta(weeks=2)),
            risk_label=label, **_interactions(rng, ability, engagement),
        ))

    descriptions = {
        "numbers": "numbers arithmetic addition subtraction foundational basic worked examples",
        "fractions": "fractions numerator denominator arithmetic equivalence visual worked examples",
        "algebra": "algebra variables expressions symbolic manipulation fractions prerequisites",
        "linear_equations": "linear equations algebra unknown variable solve balance worked examples",
        "geometry": "geometry shapes perimeter arithmetic numbers visual practice",
    }
    prefixes = {"numbers": "NUM", "fractions": "FRA", "algebra": "ALG",
                "linear_equations": "LIN", "geometry": "GEO"}
    items = []
    for topic in TOPICS:
        for variation in range(8):
            items.append(dict(
                school_id=SCHOOL_ID, course_id=COURSE_ID,
                item_id=f"{prefixes[topic]}{variation:02d}", topic_id=topic,
                description=descriptions[topic] + f" practice variant {variation}",
                approved=True, is_current=True, available_at=_iso(origin),
                created_at=_iso(origin),
            ))
    # It has usable text and no ratings from any learner.
    items.append(dict(
        school_id=SCHOOL_ID, course_id=COURSE_ID, item_id="NUM-COLD",
        topic_id="numbers", description=items[0]["description"],
        approved=True, is_current=True,
        available_at=_iso(as_of - pd.Timedelta(weeks=1)),
        created_at=_iso(as_of - pd.Timedelta(weeks=1)),
    ))
    ratings = []
    for index in range(n_students):
        student = f"S{index:03d}"
        ability, engagement = traits[student]
        early = [f"NUM{index % 4:02d}", f"NUM{(index + 1) % 4:02d}",
                 f"FRA{index % 8:02d}", f"ALG{index % 8:02d}",
                 f"GEO{index % 8:02d}"]
        if index > 0 and index % 5 == 0:
            early.append("NUM07")
        for position, item in enumerate(early):
            topic = next(row["topic_id"] for row in items if row["item_id"] == item)
            difficulty = TOPICS.index(topic) / 5
            rating = float(np.clip(5 + 3.2 * ability - 2 * difficulty + rng.normal(0, 0.9), 0, 10))
            if index == 0 and position < 2:
                rating = 8.4 - 0.3 * position
            ratings.append(dict(
                school_id=SCHOOL_ID, course_id=COURSE_ID, student_id=student,
                item_id=item, rating=rating,
                timestamp=_iso(origin + pd.Timedelta(weeks=2 + position * 2)),
            ))
        # Known-cutoff catalog, unseen student/item pairs and genuinely future
        # observations give temporal ranking evaluation a nonempty denominator.
        for item, week in (("NUM06", n_weeks - 4), ("NUM07", n_weeks - 2),
                           (f"LIN{index % 8:02d}", n_weeks - 1)):
            ratings.append(dict(
                school_id=SCHOOL_ID, course_id=COURSE_ID, student_id=student,
                item_id=item, rating=float(np.clip(7.5 + rng.normal(0, 0.7), 0, 10)),
                timestamp=_iso(origin + pd.Timedelta(weeks=week)),
            ))
    return dict(source="synthetic", as_of=_iso(as_of), events=events,
                classification=classification, items=items, ratings=ratings,
                profiles=profiles)
