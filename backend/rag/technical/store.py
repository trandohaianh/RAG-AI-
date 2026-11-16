"""Disk BM25 + child vectors, immutable per-namespace index generations.

SQLite FTS5 implements BM25; encoded tokens preserve hyphens/model identifiers.
A build is a FULL namespace snapshot. Publish only after both stores are complete.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from langchain_core.embeddings import Embeddings

from .chunking import chunk_bundle
from .models import RETRIEVAL_FORMAT, Child, LayoutBundle, Parent, Scope, TechnicalRAGError, canonical, digest


def lexical_tokens(text: str) -> list[str]:
    # Token encoding prevents FTS syntax injection and FTS hyphen splitting.
    raw = re.findall(r"[\w]+(?:[-_/][\w]+)*", text.casefold(), re.UNICODE)
    return ["t" + token.encode("utf-8").hex() for token in raw]


def _valid_vectors(rows, expected: int):
    if len(rows) != expected or not rows:
        raise TechnicalRAGError("Embedding count mismatch")
    size = len(rows[0])
    if not size or any(len(r) != size or not all(math.isfinite(float(v)) for v in r) for r in rows):
        raise TechnicalRAGError("Invalid embedding shape or values")
    return size


def _namespace_dir(root: Path, scope: Scope) -> Path:
    return Path(root) / "scopes" / scope.namespace


def _active(base: Path) -> dict:
    try:
        value = json.loads((base / "active.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not re.fullmatch(r"[0-9a-f]{32}", value.get("generation", "")):
        raise TechnicalRAGError("Invalid generation pointer")
    return value


def build_index(root: Path, bundles: list[LayoutBundle], embeddings: Embeddings,
                embedding_id: str, *, child_target: int = 200,
                parent_hard_limit: int = 12000) -> str:
    import chromadb
    from filelock import FileLock

    if not bundles or not embedding_id.strip():
        raise ValueError("Reviewed documents and a versioned embedding ID are required")
    namespace = bundles[0].scope.namespace
    if any(b.scope.namespace != namespace for b in bundles):
        raise TechnicalRAGError("A generation must belong to one tenant/workspace")
    if len({b.source_id for b in bundles}) != len(bundles):
        raise TechnicalRAGError("Duplicate source ID")
    parents, children = [], []
    for b in bundles:
        p, c = chunk_bundle(b, child_target, parent_hard_limit)
        parents.extend(p)
        children.extend(c)
    if not children:
        raise TechnicalRAGError("Empty index")
    base = _namespace_dir(root, bundles[0].scope)
    base.mkdir(parents=True, exist_ok=True)
    previous = _active(base)
    generation = uuid.uuid4().hex
    directory = base / "generations" / generation
    directory.mkdir(parents=True)
    # Failed builds are left unreferenced for inspection; never delete a live generation.
    db = directory / "corpus.sqlite"
    by_parent = {p.parent_id: p for p in parents}
    with closing(sqlite3.connect(db)) as cx, cx:
        cx.executescript("""
            PRAGMA journal_mode=DELETE;
            CREATE TABLE parents(id TEXT PRIMARY KEY, scope TEXT NOT NULL, source TEXT NOT NULL, data TEXT NOT NULL);
            CREATE TABLE children(id TEXT PRIMARY KEY, parent_id TEXT NOT NULL REFERENCES parents(id), scope TEXT NOT NULL, data TEXT NOT NULL);
            CREATE INDEX child_scope ON children(scope);
            CREATE VIRTUAL TABLE lexical USING fts5(tokens, child_id UNINDEXED, tokenize='ascii');
        """)
        cx.execute("PRAGMA foreign_keys=ON")
        cx.executemany("INSERT INTO parents VALUES (?,?,?,?)",
                       [(p.parent_id, p.scope_key, p.source_id, p.model_dump_json()) for p in parents])
        for c in children:
            cx.execute("INSERT INTO children VALUES (?,?,?,?)",
                       (c.child_id, c.parent_id, by_parent[c.parent_id].scope_key, c.model_dump_json()))
            cx.execute("INSERT INTO lexical VALUES (?,?)", (" ".join(lexical_tokens(c.retrieval_text)), c.child_id))
    client = chromadb.PersistentClient(path=str(directory / "chroma"))
    collection = client.create_collection("technical_children", metadata={"hnsw:space": "cosine"})
    dimension = None
    for start in range(0, len(children), 64):
        batch = children[start:start + 64]
        vectors = embeddings.embed_documents([c.retrieval_text for c in batch])
        size = _valid_vectors(vectors, len(batch))
        if dimension is not None and size != dimension:
            raise TechnicalRAGError("Embedding dimension changed during build")
        dimension = size
        collection.add(ids=[c.child_id for c in batch], embeddings=vectors,
                       metadatas=[{"parent_id": c.parent_id, "scope": by_parent[c.parent_id].scope_key,
                                   "generation": generation, "source": by_parent[c.parent_id].source_id} for c in batch])
    if collection.count() != len(children):
        raise TechnicalRAGError("Incomplete vector build")
    # Raw contents + approvals are represented by hashes; do not duplicate source secrets here.
    manifest = {"schema": 1, "generation": generation, "namespace": namespace,
                "embedding_id": embedding_id, "dimension": dimension, "retrieval_format": RETRIEVAL_FORMAT,
                "parents": len(parents), "children": len(children),
                "corpus_sha256": digest(db.read_bytes()),
                "sources": [{"id": b.source_id, "raw_sha256": b.raw_sha256,
                             "extraction_sha256": b.extraction_hash(),
                             "approval": b.approval.model_dump()} for b in bundles]}
    (directory / "manifest.json").write_text(canonical(manifest), encoding="utf-8")
    # Compare-and-swap: do not let a slow build overwrite a newer published snapshot.
    with FileLock(str(base / "publish.lock"), timeout=30):
        if _active(base) != previous:
            raise TechnicalRAGError("Concurrent publication; rebuild from the current complete corpus")
        temporary = base / ("active." + generation + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(canonical({"generation": generation, "manifest_sha256": digest(canonical(manifest))}))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, base / "active.json")
    return generation


class Snapshot:
    """Pin once per request (or cache by namespace+generation+embedding_id).

    Parent/child rows are always resolved against this generation, even after publish.
    """

    def __init__(self, root: Path, scope: Scope, embeddings: Embeddings, embedding_id: str):
        import chromadb

        base = _namespace_dir(root, scope)
        pointer = _active(base)
        if not pointer:
            raise TechnicalRAGError("No approved index for this namespace")
        self.generation = pointer["generation"]
        self.directory = base / "generations" / self.generation
        manifest_text = (self.directory / "manifest.json").read_text(encoding="utf-8")
        if digest(manifest_text) != pointer["manifest_sha256"]:
            raise TechnicalRAGError("Manifest hash mismatch")
        self.manifest = json.loads(manifest_text)
        if (self.manifest["namespace"] != scope.namespace
                or self.manifest["generation"] != self.generation
                or self.manifest["embedding_id"] != embedding_id
                or self.manifest.get("retrieval_format") != RETRIEVAL_FORMAT):
            raise TechnicalRAGError("Snapshot namespace, generation or embedding model mismatch")
        self.db = self.directory / "corpus.sqlite"
        if digest(self.db.read_bytes()) != self.manifest["corpus_sha256"]:
            raise TechnicalRAGError("Corpus hash mismatch")
        self.scope = scope
        self.embeddings = embeddings
        self.client = chromadb.PersistentClient(path=str(self.directory / "chroma"))
        self.collection = self.client.get_collection("technical_children")
        if self.collection.count() != self.manifest["children"]:
            raise TechnicalRAGError("Vector count mismatch")

    def connect(self):
        return sqlite3.connect(self.db.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)

    def bm25(self, query: str, k: int, source_id: str | None = None) -> list[str]:
        tokens = list(dict.fromkeys(lexical_tokens(query)))[:64]
        if not tokens:
            return []
        with closing(self.connect()) as cx:
            rows = cx.execute("""SELECT c.id FROM lexical
                JOIN children c ON c.id=lexical.child_id JOIN parents p ON p.id=c.parent_id
                WHERE lexical MATCH ? AND c.scope=? AND (? IS NULL OR p.source=?)
                ORDER BY bm25(lexical), c.id LIMIT ?""",
                (" OR ".join(tokens), self.scope.key, source_id, source_id, k)).fetchall()
        return [r[0] for r in rows]

    def vector(self, query: str, k: int, source_id: str | None = None) -> list[str]:
        embedding = self.embeddings.embed_query(query)
        if _valid_vectors([embedding], 1) != self.manifest["dimension"]:
            raise TechnicalRAGError("Query embedding dimension mismatch")
        result = self.collection.query(query_embeddings=[embedding], n_results=min(k, self.manifest["children"]),
                                       where=({"$and": [{"scope": self.scope.key}, {"source": source_id}]}
                                              if source_id else {"scope": self.scope.key}), include=["metadatas"])
        ids = result["ids"][0]
        metadata = result["metadatas"][0]
        if len(ids) != len(metadata) or len(ids) != len(set(ids)):
            raise TechnicalRAGError("Invalid vector result IDs/metadata")
        for cid, item in zip(ids, metadata):
            child, _ = self.resolve(cid)
            if (item.get("generation") != self.generation or item.get("scope") != self.scope.key
                    or item.get("parent_id") != child.parent_id
                    or source_id and item.get("source") != source_id):
                raise TechnicalRAGError("Vector metadata does not match pinned corpus")
        return ids

    def resolve(self, child_id: str) -> tuple[Child, Parent]:
        with closing(self.connect()) as cx:
            row = cx.execute("""SELECT c.data,p.data FROM children c JOIN parents p ON p.id=c.parent_id
                WHERE c.id=? AND c.scope=? AND p.scope=?""", (child_id, self.scope.key, self.scope.key)).fetchone()
        if not row:
            raise TechnicalRAGError("Missing child/parent in requested equipment scope")
        child, parent = Child.model_validate_json(row[0]), Parent.model_validate_json(row[1])
        if (child.child_id != child_id or child.parent_id != parent.parent_id
                or parent.scope_key != self.scope.key or digest(parent.text) != parent.text_sha256
                or any(len(s) != 2 or not 0 <= s[0] < s[1] <= len(parent.text) for s in child.spans)
                or "\n".join(parent.text[s:e] for s, e in child.spans) != child.text):
            raise TechnicalRAGError("Invalid child source spans or parent hash")
        return child, parent
