"""Local content/SVD recommendation with explicit prerequisite and time boundaries."""
from __future__ import annotations

from collections import defaultdict
import math

import networkx as nx
import numpy as np
import pandas as pd
from scipy import sparse
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


def _time(value):
    result = pd.Timestamp(value)
    if pd.isna(result):
        raise ValueError("A finite timestamp is required.")
    return result.tz_localize("UTC") if result.tzinfo is None else result.tz_convert("UTC")


def _scope(frame, school_id, course_id):
    result = frame.copy()
    for column, scope in (("school_id", school_id), ("course_id", course_id)):
        if column in result:
            result = result[result[column].astype(str) == str(scope)].copy()
    return result


def _required(frame, columns, name):
    missing = set(columns) - set(frame.columns)
    if missing:
        raise ValueError(f"{name} missing columns: {sorted(missing)}")


def _identifiers(frame, columns):
    for column in columns:
        if frame[column].isna().any() or not frame[column].map(lambda x: isinstance(x, str) and bool(x.strip())).all():
            raise ValueError(f"{column} must contain nonempty string identifiers.")
    return frame


class HybridRecommender:
    """Fit only on one declared school/course and observations known at cutoff.

    Catalog metadata must itself represent an as-of-cutoff snapshot. If supplied,
    available_at is enforced. Approval/version metadata remains the caller's
    trusted publication boundary, not a student-selectable preference.
    """
    def __init__(self, content_weight=0.55, positive_threshold=7.0,
                 mastery_threshold=0.7, random_state=42):
        if not 0 <= content_weight <= 1 or not 0 <= positive_threshold <= 10 or not 0 < mastery_threshold <= 1:
            raise ValueError("Invalid hybrid weights or evidence thresholds.")
        self.content_weight = float(content_weight)
        self.positive_threshold = float(positive_threshold)
        self.mastery_threshold = float(mastery_threshold)
        self.random_state = int(random_state)
        self.fitted = False

    def fit(self, items, ratings, prerequisite_edges, *, school_id, course_id, cutoff=None):
        self.fitted = False
        if school_id is None or course_id is None or not str(school_id).strip() or not str(course_id).strip():
            raise ValueError("Explicit school and course scopes are required.")
        _required(items, ["item_id", "topic_id", "description", "approved", "is_current"], "items")
        _required(ratings, ["student_id", "item_id", "rating", "timestamp"], "ratings")
        self.school_id, self.course_id = str(school_id), str(course_id)
        observations = _scope(ratings, school_id, course_id)
        observations = _identifiers(observations, ["student_id", "item_id"])
        observations["timestamp"] = pd.to_datetime(observations["timestamp"], utc=True, errors="raise")
        if observations["timestamp"].isna().any():
            raise ValueError("Ratings timestamps cannot be missing.")
        observations["rating"] = pd.to_numeric(observations["rating"], errors="raise")
        if observations["rating"].isna().any() or not np.isfinite(observations["rating"]).all() or not observations["rating"].between(0, 10).all():
            raise ValueError("Ratings must be finite observations from 0 to 10.")
        self.cutoff = _time(cutoff) if cutoff is not None else (
            observations["timestamp"].max() if len(observations) else pd.Timestamp.now(tz="UTC"))
        catalog = _scope(items, school_id, course_id)
        catalog = catalog[(catalog["approved"] == True) & (catalog["is_current"] == True)].copy()  # noqa: E712
        catalog = _identifiers(catalog, ["item_id", "topic_id"])
        if "available_at" in catalog:
            available = pd.to_datetime(catalog["available_at"], utc=True, errors="raise")
            if available.isna().any():
                raise ValueError("Catalog availability timestamps cannot be missing.")
            catalog = catalog[available <= self.cutoff].copy()
        if catalog["item_id"].duplicated().any():
            raise ValueError("Current approved item IDs must be unique within school/course.")
        if not len(catalog):
            raise ValueError("No approved current items exist at the training cutoff.")
        if not catalog["description"].map(lambda value: isinstance(value, str) and bool(value.strip())).all():
            raise ValueError("Every eligible item needs a nonempty description.")
        self.items = catalog.sort_values("item_id", kind="stable").reset_index(drop=True)
        self.graph = nx.DiGraph()
        self.graph.add_nodes_from(self.items["topic_id"].tolist())
        for edge in prerequisite_edges:
            if not isinstance(edge, (list, tuple)) or len(edge) != 2 or not all(isinstance(x, str) and x.strip() for x in edge):
                raise ValueError("Prerequisites must be (prerequisite, advanced) topic pairs.")
            self.graph.add_edge(*edge)
        if not nx.is_directed_acyclic_graph(self.graph):
            raise ValueError("The prerequisite graph must be acyclic.")
        self.topic_order = list(nx.lexicographical_topological_sort(self.graph))
        self.topic_position = {topic: index for index, topic in enumerate(self.topic_order)}
        self.ancestors = {topic: nx.ancestors(self.graph, topic) for topic in self.graph}
        self.vectorizer = TfidfVectorizer(ngram_range=(1, 2), max_features=20000)
        try:
            self.matrix = self.vectorizer.fit_transform(self.items["description"])
        except ValueError as exc:
            if "empty vocabulary" not in str(exc):
                raise
            self.vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), max_features=20000)
            self.matrix = self.vectorizer.fit_transform(self.items["description"])
        observations = observations[(observations["timestamp"] <= self.cutoff)
                                    & observations["item_id"].isin(self.items["item_id"])].copy()
        self.ratings = observations.sort_values(["timestamp", "student_id", "item_id"], kind="stable").drop_duplicates(
            ["student_id", "item_id"], keep="last").reset_index(drop=True)
        self.item_position = {item: index for index, item in enumerate(self.items["item_id"])}
        self.known_students = set(self.ratings["student_id"])
        self.known_items = set(self.ratings["item_id"])
        self.popularity = self.ratings.groupby("item_id")["rating"].mean().to_dict()
        self.svd = None
        if len(self.ratings):
            try:
                from surprise import Dataset, Reader, SVD
            except ImportError as exc:
                raise ImportError("Surprise SVD is required. Install engine/requirements.txt in its isolated environment.") from exc
            dataset = Dataset.load_from_df(self.ratings[["student_id", "item_id", "rating"]],
                                           Reader(rating_scale=(0, 10)))
            self.svd = SVD(n_factors=20, n_epochs=30, random_state=self.random_state)
            self.svd.fit(dataset.build_full_trainset())
        self.fitted = True
        return self

    def _check(self):
        if not self.fitted:
            raise RuntimeError("Fit the scoped recommender before using it.")

    def _evidence(self, mastery, weak_topics):
        mastery = dict(mastery or {})
        for topic, value in mastery.items():
            if topic not in self.graph:
                raise ValueError(f"Unknown mastery topic: {topic}")
            if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)) or not np.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("Mastery evidence must be finite observations from 0 to 1.")
        if weak_topics is None:
            weak = {topic for topic, value in mastery.items() if value < self.mastery_threshold}
        else:
            if isinstance(weak_topics, str):
                raise ValueError("weak_topics must be a sequence of topic IDs.")
            if not isinstance(weak_topics, (list, tuple, set)) or not all(isinstance(topic, str) for topic in weak_topics):
                raise ValueError("weak_topics must contain topic IDs.")
            weak = set(weak_topics)
            if not weak <= set(self.graph):
                raise ValueError("Weak topics must belong to this course graph.")
        needed = set()
        for topic in weak:
            needed.update(self.ancestors[topic])
            needed.add(topic)
        return mastery, weak, needed

    def eligible_items(self, student_id, *, mastery=None, weak_topics=None, completed_items=None):
        """Return prerequisite-eligible unseen IDs, without inferring unknown mastery."""
        self._check()
        if not isinstance(student_id, str) or not student_id.strip():
            raise ValueError("A nonempty student identifier is required.")
        if completed_items is not None and (isinstance(completed_items, str) or
                not isinstance(completed_items, (list, tuple, set)) or
                not all(isinstance(item, str) and item.strip() for item in completed_items)):
            raise ValueError("completed_items must contain item IDs.")
        mastery, weak, needed = self._evidence(mastery, weak_topics)
        attempted = set(self.ratings.loc[self.ratings["student_id"] == str(student_id), "item_id"])
        attempted.update(completed_items or [])
        eligible = []
        for row in self.items.itertuples(index=False):
            if row.item_id in attempted or (needed and row.topic_id not in needed):
                continue
            if mastery.get(row.topic_id, -1) >= self.mastery_threshold and row.topic_id not in weak:
                continue
            if any(mastery.get(prerequisite, -1) < self.mastery_threshold
                   for prerequisite in self.ancestors[row.topic_id]):
                continue
            eligible.append(row.item_id)
        return eligible

    def _content_scores(self, student_id, interests):
        history = self.ratings[(self.ratings["student_id"] == str(student_id))
                               & (self.ratings["rating"] >= self.positive_threshold)]
        if len(history):
            positions = [self.item_position[item] for item in history["item_id"]]
            weights = history["rating"].to_numpy(dtype=float) / 10
            profile = sparse.csr_matrix(np.asarray(self.matrix[positions].multiply(weights[:, None]).sum(axis=0)))
            source = "positive_history"
        elif interests:
            if isinstance(interests, str):
                query = interests
            elif isinstance(interests, (list, tuple)) and all(isinstance(value, str) for value in interests):
                query = " ".join(interests)
            else:
                raise ValueError("interests must be text or a list of text interests.")
            if len(query) > 20000:
                raise ValueError("Interest text is too long.")
            profile = self.vectorizer.transform([query])
            source = "explicit_interests"
        else:
            return np.zeros(len(self.items)), None
        if profile.nnz == 0:
            return np.zeros(len(self.items)), None
        return np.clip(cosine_similarity(profile, self.matrix).ravel(), 0, 1), source

    def recommend(self, student_id, k=5, *, mastery=None, interests=None, weak_topics=None,
                  completed_items=None, strategy="hybrid"):
        """Scores are ranking heuristics, not calibrated success probabilities."""
        self._check()
        if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100:
            raise ValueError("k must be an integer from 1 to 100.")
        if strategy not in {"hybrid", "content", "popularity"}:
            raise ValueError("Unknown recommendation strategy.")
        if not isinstance(student_id, str) or not student_id.strip():
            raise ValueError("A nonempty student identifier is required.")
        mastery, weak, needed = self._evidence(mastery, weak_topics)
        eligible = set(self.eligible_items(student_id, mastery=mastery, weak_topics=list(weak),
                                           completed_items=completed_items))
        content, profile_source = self._content_scores(student_id, interests)
        candidates = []
        for row in self.items.itertuples(index=False):
            if row.item_id not in eligible:
                continue
            content_score = float(content[self.item_position[row.item_id]])
            known = bool(self.svd is not None and student_id in self.known_students and row.item_id in self.known_items)
            collaborative = None
            if known and strategy == "hybrid":
                collaborative = float(np.clip(self.svd.predict(student_id, row.item_id).est / 10, 0, 1))
            popularity = float(self.popularity.get(row.item_id, 0)) / 10
            if strategy == "popularity":
                score, source = popularity, "population_rating_mean"
            elif strategy == "content":
                score, source = content_score, profile_source or "no_content_profile"
            elif profile_source and collaborative is not None:
                score = self.content_weight * content_score + (1 - self.content_weight) * collaborative
                source = "hybrid_svd_content"
            elif profile_source:
                score, source = content_score, ("content_new_item" if row.item_id not in self.known_items else "content_cold_student")
            elif collaborative is not None:
                score, source = collaborative, "collaborative_history"
            else:
                score, source = popularity, ("popularity_cold_start" if row.item_id in self.known_items else "catalog_no_history")
            path = [topic for topic in self.topic_order
                    if topic in self.ancestors[row.topic_id] or topic == row.topic_id]
            reason = source
            if needed:
                reason += "; unmet prerequisite frontier before requested weak topics"
            if not self.ancestors[row.topic_id] and row.topic_id not in mastery:
                reason += "; foundational topic, mastery not yet observed"
            candidates.append(dict(
                item_id=row.item_id, topic_id=row.topic_id, score=float(score),
                content_score=content_score, collaborative_score=collaborative,
                collaborative_known=known, source=source, reason=reason,
                prerequisite_path=path,
            ))
        # Eligibility already prohibits advancing over any unmet ancestor.
        # In a remediation path, topological priority also preserves its order.
        candidates.sort(key=lambda item: (
            self.topic_position[item["topic_id"]] if needed else 0,
            -item["score"], item["item_id"]))
        return candidates[:k]


def _errors(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    if not len(actual):
        return None
    difference = actual - predicted
    return {"mae": float(np.mean(np.abs(difference))),
            "rmse": float(np.sqrt(np.mean(np.square(difference)))), "n": int(len(actual))}


def evaluate_temporal(items, ratings, prerequisite_edges, *, school_id, course_id,
                      cutoff, k=5, mastery_by_student=None, interests_by_student=None,
                      weak_topics_by_student=None, recommender_kwargs=None):
    """Evaluate future observations without fitting on them.

    Mastery/interests are optional *as-of-cutoff* evidence, never derived from
    holdout outcomes. Ranking denominators include only eligible unseen positive
    items present in the training-cutoff catalog. Excluded positives are reported
    separately, so conservative prerequisite gating cannot silently inflate recall.
    """
    if cutoff is None:
        raise ValueError("Temporal evaluation requires an explicit cutoff.")
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100:
        raise ValueError("k must be an integer from 1 to 100.")
    model = HybridRecommender(**(recommender_kwargs or {})).fit(
        items, ratings, prerequisite_edges, school_id=school_id, course_id=course_id, cutoff=cutoff)
    if not len(model.ratings):
        raise ValueError("Temporal evaluation needs historical training ratings.")
    future = _scope(ratings, school_id, course_id)
    future["timestamp"] = pd.to_datetime(future["timestamp"], utc=True, errors="raise")
    future["rating"] = pd.to_numeric(future["rating"], errors="raise")
    future = future[future["timestamp"] > model.cutoff].sort_values(
        ["timestamp", "student_id", "item_id"], kind="stable").drop_duplicates(
            ["student_id", "item_id"], keep="first")
    if future["rating"].isna().any() or not np.isfinite(future["rating"]).all() or not future["rating"].between(0, 10).all():
        raise ValueError("Future evaluation ratings must be finite observations from 0 to 10.")
    mastery_by_student = mastery_by_student or {}
    interests_by_student = interests_by_student or {}
    weak_topics_by_student = weak_topics_by_student or {}
    catalog_ids = set(model.items["item_id"])
    known_future = future[future["item_id"].isin(catalog_ids)]
    actual, svd_predictions, mean_predictions, item_predictions = [], [], [], []
    global_mean = float(model.ratings["rating"].mean())
    warm_pairs = 0
    for row in known_future.itertuples(index=False):
        actual.append(float(row.rating))
        svd_predictions.append(float(model.svd.predict(row.student_id, row.item_id).est))
        mean_predictions.append(global_mean)
        item_predictions.append(float(model.popularity.get(row.item_id, global_mean)))
        warm_pairs += int(row.student_id in model.known_students and row.item_id in model.known_items)
    metrics = {strategy: {"recall_at_k": [], "ndcg_at_k": []}
               for strategy in ("hybrid", "content", "popularity")}
    train_pairs = set(zip(model.ratings["student_id"], model.ratings["item_id"]))
    positives = defaultdict(set)
    raw_positive_unseen = 0
    for row in future.itertuples(index=False):
        if row.rating >= model.positive_threshold and (row.student_id, row.item_id) not in train_pairs:
            positives[row.student_id].add(row.item_id)
            raw_positive_unseen += 1
    eligible_positive_pairs = 0
    evaluated_users = 0
    for student_id, raw_targets in sorted(positives.items()):
        evidence = dict(
            mastery=mastery_by_student.get(student_id),
            interests=interests_by_student.get(student_id),
            weak_topics=weak_topics_by_student.get(student_id),
        )
        eligible = set(model.eligible_items(student_id, mastery=evidence["mastery"],
                                            weak_topics=evidence["weak_topics"]))
        targets = raw_targets & eligible
        eligible_positive_pairs += len(targets)
        if not targets:
            continue
        evaluated_users += 1
        ideal = sum(1 / math.log2(index + 2) for index in range(min(k, len(targets))))
        for strategy in metrics:
            ranked = model.recommend(student_id, k=k, strategy=strategy, **evidence)
            hits = [int(row["item_id"] in targets) for row in ranked]
            recall = sum(hits) / len(targets)
            ndcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits)) / ideal
            metrics[strategy]["recall_at_k"].append(recall)
            metrics[strategy]["ndcg_at_k"].append(ndcg)
    ranking = {
        strategy: {
            metric: float(np.mean(values)) if values else None for metric, values in results.items()
        } | {"evaluated_users": evaluated_users}
        for strategy, results in metrics.items()
    }
    return {
        "cutoff": model.cutoff.isoformat(),
        "train_ratings": int(len(model.ratings)),
        "future_unique_pairs": int(len(future)),
        "rating_evaluated_pairs": int(len(known_future)),
        "rating_warm_pairs": warm_pairs,
        "rating_cold_pairs": int(len(known_future)) - warm_pairs,
        "future_pairs_outside_cutoff_catalog": int(len(future) - len(known_future)),
        "rating_metrics": {
            "svd": _errors(actual, svd_predictions),
            "global_mean": _errors(actual, mean_predictions),
            "item_mean": _errors(actual, item_predictions),
        },
        "ranking_metrics": ranking,
        "ranking_k": k,
        "positive_future_unseen_pairs": raw_positive_unseen,
        "eligible_positive_pairs": eligible_positive_pairs,
        "excluded_positive_pairs": raw_positive_unseen - eligible_positive_pairs,
        "users_with_positive_unseen": len(positives),
        "users_without_eligible_positive": len(positives) - evaluated_users,
        "ranking_denominator": "Per-user eligible unseen future-positive items present in the cutoff catalog; macro-average over users with at least one.",
        "mastery_boundary": "Caller-supplied evidence must have been observed by the cutoff; unknown prerequisites stay blocked.",
        "limitations": "Synthetic or observational ranking accuracy does not establish an educational benefit or guarantee real-world accuracy.",
    }
