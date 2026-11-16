"""Offline tests: real SQLite FTS5 + Chroma, injected embeddings/reranker/selector.

No paid API, downloaded weights, application DB or customer documents.
"""
import json
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.embeddings import Embeddings

from rag.technical.adapters import CohereReranker, LocalBGEReranker, validate_scores
from rag.technical.chunking import chunk_bundle
from rag.technical.layout import approve, table_layout
from rag.technical.models import Approval, Block, LayoutBundle, REFUSAL, Scope, Selection, TechnicalRAGError, digest
from rag.technical.pipeline import Policy, TechnicalRAG, exact_match, rrf
from rag.technical.store import Snapshot, build_index


SCOPE = Scope(tenant="demo", workspace="fieldops", vendor="SYNTHETIC", model="LAB-X01",
              hardware_revision="demo-r1", manual_revision="training-v1")


def bundle(text="Error 504: ví dụ giả lập; E01; −48 V; 0.50 A. Không thao tác trên thiết bị.",
           source="demo-1", blocks=None, scope=SCOPE):
    b = LayoutBundle(source_id=source, source_name=source + ".pdf", raw_sha256=digest("raw:" + text),
                     parser="test:1", scope=scope, scope_reviewed=True,
                     blocks=blocks or [Block(kind="text", text=text, section="procedure-1", pages=[2],
                                             single_equipment=True)])
    return b.model_copy(update={"approval": Approval(reviewer="test-reviewer", reviewed_at="2026-10-26",
                           raw_sha256=b.raw_sha256, extraction_sha256=b.extraction_hash())})


class FakeEmbeddings(Embeddings):
    def embed_documents(self, texts):
        return [[float("504" in t), float("E01" in t), 0.25, 1.0] for t in texts]

    def embed_query(self, text):
        return self.embed_documents([text])[0]


class FakeReranker:
    identity = "test-reranker:v1"

    def __init__(self, scores=None):
        self.calls = []
        self.scores = scores

    def score(self, query, docs):
        self.calls.append((query, docs))
        return self.scores if self.scores is not None else [0.9] * len(docs)


class FakeSelector:
    def __init__(self, value=None):
        self.value = value
        self.calls = []

    def select(self, query, scope, context):
        self.calls.append(context)
        return self.value if self.value is not None else Selection(enough_information=True,
                                               evidence_ids=[c["id"] for c in context])


def service(root, reranker=None, selector=None, **settings):
    r = reranker or FakeReranker()
    policy = Policy(reranker_id=r.identity, calibration_id="SYNTHETIC-TEST-ONLY", min_score=0.4, **settings)
    return TechnicalRAG(root, FakeEmbeddings(), "fake:v1", r, selector or FakeSelector(), policy)


@pytest.fixture(scope="module")
def indexed(tmp_path_factory):
    root = tmp_path_factory.mktemp("technical-index")
    build_index(root, [bundle()], FakeEmbeddings(), "fake:v1")
    return root


@pytest.mark.parametrize("query,text,expected", [
    ("E01", "E01", True), ("E01", "E1", False), ("E01", "E010", False),
    ("Error 504", "Error 5040", False), ("Error 504", "Error 504", True),
    ("−48 V", "48 V", False), ("48 V", "−48 V", False), ("0.50 A", "0.50A", True),
    ("0.50 A", "0.5 A", False), ("0.5 A", "500 mA", False),
    ("0.5 A", "5 A", False), ("LAB-X01", "LAB-X010", False),
])
def test_exact_anchors(query, text, expected):
    assert exact_match(query, text) is expected


def test_comma_decimal_abstains(indexed):
    result = service(indexed).answer("E01 0,5 A", SCOPE)
    assert result.text == REFUSAL and result.status == "abstained"


def test_bm25_does_not_execute_query_operators(indexed):
    snapshot = Snapshot(indexed, SCOPE, FakeEmbeddings(), "fake:v1")
    assert snapshot.bm25('E01 OR (NOT " AND)', 5)
    assert snapshot.bm25("Error 504", 5)
    assert snapshot.vector("Error 504", 5)


def test_rrf_does_not_give_duplicates_extra_votes():
    assert rrf([["a"] * 100 + ["b"], ["b"]], 2) == ["b", "a"]


def test_long_procedure_and_warnings_stay_one_parent():
    text = "ĐIỀU KIỆN DEMO\n" + "Bước mô phỏng E01, không dùng vận hành.\n" * 80 + "CẢNH BÁO CUỐI"
    parents, children = chunk_bundle(bundle(text))
    assert len(parents) == 1 and len(parents[0].text) > 2000
    assert parents[0].text == text
    assert all("\n".join(text[s:e] for s, e in c.spans) == c.text for c in children)
    covered = {i for c in children for s, e in c.spans for i in range(s, e)}
    assert covered == set(range(len(text)))
    assert parents[0].text.endswith("CẢNH BÁO CUỐI")


def test_oversize_atomic_section_is_never_truncated():
    with pytest.raises(TechnicalRAGError, match="Oversize"):
        chunk_bundle(bundle("E01 " * 4000))


def test_table_rows_headers_and_footnote_preserved():
    html, spans, headers = table_layout("<table><tr><th>Mã</th><th>A</th></tr>"
        "<tr><td>E01</td><td>0.50 A</td></tr><tr><td>E02</td><td>2.5 A</td></tr></table>")
    blocks = [Block(kind="table", text=html, section="table-1", row_spans=spans,
                    header_rows=headers, single_equipment=True),
              Block(kind="text", text="Chú thích: số liệu giả lập, không áp dụng thiết bị.",
                    section="table-1", single_equipment=True)]
    parents, children = chunk_bundle(bundle(blocks=blocks))
    rows = [c for c in children if c.anchor_eligible and "<tr>" in c.text]
    assert len(rows) == 2 and all("<th>Mã</th>" in c.text for c in rows)
    assert not any(exact_match("E01 2.5 A", c.text) for c in children if c.anchor_eligible)
    assert "Chú thích" in parents[0].text and html in parents[0].text


@pytest.mark.parametrize("markup", [
    '<table><tr><td colspan="2">E01</td></tr></table>',
    '<table><tr><td rowspan="2">E01</td></tr></table>',
    '<table><tr><td>E01</tr></td></table>',
    '<table><tr><td><script>E01</script></td></tr></table>',
])
def test_unsupported_table_requires_review(markup):
    with pytest.raises(TechnicalRAGError):
        table_layout(markup)


def test_mutated_text_or_scope_invalidates_approval():
    b = bundle()
    for changed in [b.model_copy(update={"scope": SCOPE.model_copy(update={"model": "OTHER"})}),
                    b.model_copy(update={"blocks": [b.blocks[0].model_copy(update={"text": "5 A"})]})]:
        with pytest.raises(TechnicalRAGError, match="Unreviewed"):
            chunk_bundle(changed)


def test_unreviewed_diagram_is_blocked():
    with pytest.raises(TechnicalRAGError):
        chunk_bundle(bundle(blocks=[Block(kind="diagram", text="figure", section="s", single_equipment=True)]))


def test_approve_checks_raw_file_and_flags(tmp_path):
    raw = tmp_path / "manual.pdf"
    raw.write_bytes(b"raw")
    b = bundle().model_copy(update={"raw_sha256": digest(b"raw"), "approval": None})
    assert approve(b, raw, "engineer").approval.reviewer == "engineer"
    raw.write_bytes(b"changed")
    with pytest.raises(TechnicalRAGError, match="changed"):
        approve(b, raw, "engineer")


def test_pipeline_returns_exact_whole_parent_and_always_reranks(indexed):
    reranker, selector = FakeReranker(), FakeSelector()
    result = service(indexed, reranker, selector).answer("Error 504 E01", SCOPE)
    assert result.status == "evidence" and reranker.calls
    assert len(reranker.calls[0][1]) == 1
    assert result.evidence[0].parent.text == bundle().blocks[0].text
    assert result.text.endswith(bundle().blocks[0].text)
    assert result.semantic_correctness_verified is False and result.requires_engineer_review


def test_unknown_code_does_not_reach_reranker_or_llm(indexed):
    reranker, selector = FakeReranker(), FakeSelector()
    result = service(indexed, reranker, selector).answer("Error 5040", SCOPE)
    assert result.status == "abstained" and result.text == REFUSAL
    assert not reranker.calls and not selector.calls


@pytest.mark.parametrize("value", [
    {"enough_information": True, "evidence_ids": ["E999"]},
    {"enough_information": True, "evidence_ids": ["E1", "E1"]},
    {"enough_information": True, "evidence_ids": ["E1"], "answer": "100 A"},
    {"enough_information": False, "evidence_ids": []},
])
def test_invalid_model_selection_cannot_emit_free_text(indexed, value):
    result = service(indexed, selector=FakeSelector(value)).answer("E01", SCOPE)
    assert result.text == REFUSAL and not result.evidence


@pytest.mark.parametrize("scores", [[float("nan")], [float("inf")], [True], [1.5], []])
def test_invalid_reranker_scores_fail_closed(indexed, scores):
    result = service(indexed, reranker=FakeReranker(scores)).answer("E01", SCOPE)
    assert result.text == REFUSAL


def test_provider_failure_has_no_lexical_fallback(indexed):
    class Broken(FakeReranker):
        def score(self, query, docs):
            raise TimeoutError("provider secret must not appear")
    result = service(indexed, reranker=Broken()).answer("E01", SCOPE)
    assert result.status == "abstained" and "secret" not in result.model_dump_json()


def test_wrong_hardware_and_tenant_fail_closed(indexed):
    for scope in [SCOPE.model_copy(update={"hardware_revision": "other"}),
                  SCOPE.model_copy(update={"tenant": "other"})]:
        assert service(indexed).answer("E01", scope).text == REFUSAL


def test_wrong_embedding_model_is_blocked(indexed):
    with pytest.raises(TechnicalRAGError, match="embedding"):
        Snapshot(indexed, SCOPE, FakeEmbeddings(), "different:v2")


def test_snapshot_pinned_while_new_generation_is_published(tmp_path):
    old = bundle("E01: OLD DEMO TEXT")
    build_index(tmp_path, [old], FakeEmbeddings(), "fake:v1")
    pinned = Snapshot(tmp_path, SCOPE, FakeEmbeddings(), "fake:v1")
    cid = pinned.bm25("E01", 1)[0]
    build_index(tmp_path, [bundle("E01: NEW DEMO TEXT")], FakeEmbeddings(), "fake:v1")
    current = Snapshot(tmp_path, SCOPE, FakeEmbeddings(), "fake:v1")
    assert pinned.generation != current.generation
    assert pinned.resolve(cid)[1].text == old.blocks[0].text


def test_failed_build_keeps_old_active_generation(indexed):
    old = Snapshot(indexed, SCOPE, FakeEmbeddings(), "fake:v1").generation
    class BrokenEmbeddings(FakeEmbeddings):
        def embed_documents(self, texts):
            raise RuntimeError("failed build")
    with pytest.raises(RuntimeError):
        build_index(indexed, [bundle("E01 updated")], BrokenEmbeddings(), "fake:v1")
    assert Snapshot(indexed, SCOPE, FakeEmbeddings(), "fake:v1").generation == old


def test_corpus_tampering_is_detected(tmp_path):
    gen = build_index(tmp_path, [bundle()], FakeEmbeddings(), "fake:v1")
    db = tmp_path / "scopes" / SCOPE.namespace / "generations" / gen / "corpus.sqlite"
    with db.open("ab") as f:
        f.write(b"tamper")
    assert service(tmp_path).answer("E01", SCOPE).text == REFUSAL


def test_parent_context_budget_abstains_instead_of_slicing(tmp_path):
    build_index(tmp_path, [bundle("E01 " + "whole procedure " * 100)], FakeEmbeddings(), "fake:v1")
    assert service(tmp_path, context_char_budget=1000).answer("E01", SCOPE).text == REFUSAL


@pytest.mark.parametrize("results", [
    [{"index": True, "relevance_score": 0.9}],
    [{"index": 3, "relevance_score": 0.9}],
    [{"index": 0, "relevance_score": float("nan")}],
    [],
])
def test_cohere_response_validation(results):
    def handler(request):
        return httpx.Response(200, content=json.dumps({"results": results}).encode())
    adapter = CohereReranker("not-a-real-key", transport=httpx.MockTransport(handler))
    with pytest.raises(TechnicalRAGError):
        adapter.score("E01", ["E01"])


def test_cohere_v2_explicit_budget_and_result_order():
    def handler(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v2/rerank" and payload["max_tokens_per_doc"] == 8192
        return httpx.Response(200, json={"results": [
            {"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.2}]})
    adapter = CohereReranker("not-a-real-key", transport=httpx.MockTransport(handler))
    assert adapter.score("E01", ["first", "second"]) == [0.2, 0.9]
    with pytest.raises(TechnicalRAGError, match="budgets"):
        adapter.score("E01", ["x" * 4097])


def test_local_reranker_checks_combined_token_budget_without_truncation():
    adapter = LocalBGEReranker.__new__(LocalBGEReranker)
    adapter.lock = threading.Lock()
    adapter.torch = SimpleNamespace(inference_mode=nullcontext)
    adapter.batch_size, adapter.max_pair_tokens = 8, 512
    def tokenizer(*args, **kwargs):
        assert kwargs["truncation"] is False
        return {"input_ids": SimpleNamespace(shape=(1, 513))}
    adapter.tokenizer = tokenizer
    with pytest.raises(TechnicalRAGError, match="too long"):
        adapter.score("query", ["document"])


@pytest.mark.parametrize("reason,parsing_error", [("MAX_TOKENS", None), ("SAFETY", None), (None, None), ("STOP", ValueError("bad json"))])
def test_langchain_selector_rejects_unfinished_or_invalid_output(reason, parsing_error):
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from rag.technical.adapters import LangChainSelector
    class Chat:
        def with_structured_output(self, schema, include_raw):
            assert schema is Selection and include_raw is True
            return RunnableLambda(lambda prompt: {
                "raw": AIMessage(content="", response_metadata={"finish_reason": reason}),
                "parsed": Selection(enough_information=True, evidence_ids=["E1"]),
                "parsing_error": parsing_error})
    with pytest.raises(TechnicalRAGError):
        LangChainSelector(Chat()).select("E01", SCOPE, [])


def test_langchain_selector_system_prompt_and_stop():
    from langchain_core.messages import AIMessage
    from langchain_core.runnables import RunnableLambda
    from rag.technical.adapters import LangChainSelector
    class Chat:
        def with_structured_output(self, schema, include_raw):
            def invoke(prompt):
                messages = prompt.to_messages()
                assert REFUSAL in messages[0].content
                assert "CONTEXT=" in messages[1].content
                return {"raw": AIMessage(content="", response_metadata={"finish_reason": "STOP"}),
                        "parsed": Selection(enough_information=True, evidence_ids=["E1"]),
                        "parsing_error": None}
            return RunnableLambda(invoke)
    assert LangChainSelector(Chat()).select("E01", SCOPE, []).evidence_ids == ["E1"]


def test_table_keeps_empty_cell_and_rejects_ragged_rows():
    text, _, _ = table_layout("<table><tr><th>Model</th><th>A</th><th>V</th></tr>"
                              "<tr><td>LAB-X01</td><td></td><td>−48 V</td></tr></table>")
    assert "<td></td><td>−48 V</td>" in text
    with pytest.raises(TechnicalRAGError):
        table_layout("<table><tr><th>A</th><th>V</th></tr><tr><td>48</td></tr></table>")


def test_build_rejects_partial_row_offsets():
    text, spans, headers = table_layout("<table><tr><th>Code</th></tr><tr><td>E01</td></tr></table>")
    spans[-1][0] += 1
    b = bundle(blocks=[Block(kind="table", text=text, section="s", row_spans=spans,
                             header_rows=headers, single_equipment=True)])
    with pytest.raises(TechnicalRAGError, match="complete canonical rows"):
        chunk_bundle(b)


def test_branch_failure_blocks_selector(indexed, monkeypatch):
    def broken(*args):
        raise TimeoutError("vector unavailable")
    monkeypatch.setattr(Snapshot, "vector", broken)
    selector = FakeSelector()
    result = service(indexed, selector=selector).answer("E01", SCOPE)
    assert result.text == REFUSAL and not selector.calls


def test_source_filter_is_applied_in_both_branches(tmp_path):
    build_index(tmp_path, [bundle(source="one"), bundle(source="two")], FakeEmbeddings(), "fake:v1")
    snapshot = Snapshot(tmp_path, SCOPE, FakeEmbeddings(), "fake:v1")
    for ids in [snapshot.vector("E01", 10, "one"), snapshot.bm25("E01", 10, "one")]:
        assert ids and all(snapshot.resolve(i)[1].source_id == "one" for i in ids)
    assert service(tmp_path).answer("E01", SCOPE).text == REFUSAL
    assert service(tmp_path).answer("E01", SCOPE, "one").status == "evidence"



def test_publish_pointer_failure_keeps_previous_generation(tmp_path, monkeypatch):
    import rag.technical.store as store
    previous = build_index(tmp_path, [bundle()], FakeEmbeddings(), "fake:v1")
    real_replace = store.os.replace
    def fail_pointer(src, dst):
        if Path(dst).name == "active.json":
            raise OSError("simulated crash before pointer publication")
        return real_replace(src, dst)
    monkeypatch.setattr(store.os, "replace", fail_pointer)
    with pytest.raises(OSError):
        build_index(tmp_path, [bundle("E01 newer")], FakeEmbeddings(), "fake:v1")
    assert Snapshot(tmp_path, SCOPE, FakeEmbeddings(), "fake:v1").generation == previous


def test_vector_parent_metadata_must_match_pinned_corpus(tmp_path):
    build_index(tmp_path, [bundle()], FakeEmbeddings(), "fake:v1")
    snapshot = Snapshot(tmp_path, SCOPE, FakeEmbeddings(), "fake:v1")
    child_id = snapshot.bm25("E01", 1)[0]
    snapshot.collection.update(ids=[child_id], metadatas=[{"parent_id": "wrong-parent"}])
    with pytest.raises(TechnicalRAGError, match="metadata"):
        snapshot.vector("E01", 5)


def test_gemini_structured_adapter_constructs_without_network():
    from langchain_google_genai import ChatGoogleGenerativeAI
    from rag.technical.adapters import LangChainSelector
    model = ChatGoogleGenerativeAI(model="gemini-test-only-no-invoke", api_key="fake-not-a-real-key",
                                   temperature=0, timeout=60, max_retries=0)
    assert LangChainSelector(model).chain is not None



def test_confirmed_asset_prefix_is_search_only_not_a_quote(indexed):
    snapshot = Snapshot(indexed, SCOPE, FakeEmbeddings(), "fake:v1")
    # Existing fixture built at module start by the current chunker.
    child, parent = snapshot.resolve(snapshot.bm25("E01", 1)[0])
    assert "LAB-X01" in child.retrieval_text and "LAB-X01" not in child.text
    assert "LAB-X01" not in parent.text
    assert service(indexed).answer("LAB-X01 Error 504 E01", SCOPE).status == "evidence"


def test_scope_model_never_substitutes_an_explicit_error_code():
    assert not exact_match("Error E01", "some other error", confirmed_model="E01")
    assert not exact_match("E01", "some other error", confirmed_model="E01")
    assert exact_match("model E01 Error 504", "Error 504", confirmed_model="E01")



def test_search_prefix_cannot_satisfy_a_quantity_anchor(indexed):
    snapshot = Snapshot(indexed, SCOPE, FakeEmbeddings(), "fake:v1")
    child, _ = snapshot.resolve(snapshot.bm25("E01", 1)[0])
    forged_prefix = child.model_copy(update={"context_prefix": "Model LAB-X01 99 A"})
    assert "99 A" in forged_prefix.retrieval_text
    assert not exact_match("E01 99 A", forged_prefix.text, SCOPE.model)
