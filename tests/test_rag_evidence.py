"""Evidence identity, version snapshots, persistence, citations and live access rights."""
import asyncio
import copy
import json

import pytest

import config
import rag.chain as chain
import rag.cache as cache
import chat.router as chat
import embed.router as embed
import public_api.router as public_api
from rag.evidence import EvidenceChunk, RetrievedChunks, capture_evidence, audit_citations, read_bundle
from rag.bm25 import reciprocal_rank_fusion
from rag.document_lifecycle import storage_lock, advance_revision
from chat.models import Message
from field.models import FieldWorkspaceMember
from test_query_quota import quota_db, quota_client  # noqa: F401

REAL_ONCE = chain.answer_rag_once


def snapshot(revision="a" * 32, collection="user_1"):
    return RetrievedChunks([EvidenceChunk("Nguồn nguyên văn cho kiểm thử.", "SOP.pdf", 3,
        chunk_id="real-chroma-chunk-id", document_version="document-version-1")], collection=collection, revision=revision)


def frames(response):
    assert response.status_code == 200, response.text
    return [json.loads(frame[6:]) for frame in response.text.strip().split("\n\n")]


def test_snapshot_preserves_real_chunk_id_and_does_not_invent_missing_ids():
    bundle = capture_evidence(copy.deepcopy(snapshot()))
    assert bundle['evidence'][0]['chunk_id'] == "real-chroma-chunk-id"
    assert bundle['evidence'][0]['quote'] == "Nguồn nguyên văn cho kiểm thử."
    assert bundle['evidence'][0]['collection_revision'] == "a" * 32
    assert capture_evidence([("text", "file.txt", None)])['evidence'][0]['chunk_id'] is None


def test_same_file_and_page_distinct_chunks_are_not_collapsed():
    rows = [EvidenceChunk("same text", "SOP.pdf", 3, chunk_id="one"),
            EvidenceChunk("same text", "SOP.pdf", 3, chunk_id="two")]
    merged = reciprocal_rank_fusion([rows, rows], k=5)
    assert [row.chunk_id for row in merged] == ['one', 'two']
    assert len(capture_evidence(merged)['evidence']) == 2


@pytest.mark.parametrize("answer,status", [("Theo nguồn 【E1】", "cited"), ("Không có nhãn", "uncited"),
    ("Nguồn 【E999】", "invalid"), ("Nguồn 【E0】", "invalid"), ("Nguồn 【E01】", "invalid")])
def test_citation_identity_is_checked_without_claiming_semantic_accuracy(answer, status):
    audit = audit_citations(answer, capture_evidence(snapshot()))
    assert audit['status'] == status
    assert audit['semantic_support_verified'] is False


def test_empty_legacy_history_does_not_reconstruct_current_evidence():
    assert read_bundle(None)['evidence'] == []
    assert read_bundle("not json")['evidence'] == []


def test_redis_cache_roundtrip_preserves_provenance(monkeypatch):
    class Redis:
        def setex(self, key, ttl, value): self.value = value
        def get(self, key): return self.value
    redis = Redis()
    monkeypatch.setattr(cache, '_redis', lambda: redis)
    cache.set_cached_chunks(1, "revision:query", 5, snapshot())
    cached = cache.get_cached_chunks(1, "revision:query", 5)
    assert cached[0].chunk_id == "real-chroma-chunk-id"
    assert cached[0].document_version == "document-version-1"


@pytest.mark.parametrize("mutate_during_generation", [False, True])
def test_chat_reload_keeps_original_snapshot_and_checks_version(quota_client, quota_db, monkeypatch, tmp_path, mutate_during_generation):
    client, _, actor = quota_client
    index = tmp_path / "chroma"; index.mkdir()
    monkeypatch.setattr(config, 'CHROMA_DIR', index)
    with storage_lock(index, 'user_1'):
        old_revision = advance_revision(index, 'user_1')
    async def retrieve(*args, **kwargs): return snapshot(old_revision)
    def provider(prompt):
        assert '【E1】' in prompt
        yield "Theo nguồn 【E1】."
        if mutate_during_generation:
            with storage_lock(index, 'user_1'):
                advance_revision(index, 'user_1')
    monkeypatch.setattr(chain, 'retrieve', retrieve)
    monkeypatch.setattr(chain, '_iter_tokens', provider)
    monkeypatch.setattr(chat, 'stream_rag_answer', chain.stream_rag_answer)
    done = next(frame for frame in frames(client.post('/chat/1', json={'question':'Kiểm tra?'})) if frame.get('done'))
    assert done['sources'] == ['SOP.pdf']
    assert done['evidence'][0]['collection_revision'] == old_revision
    assert done['citation_audit']['cited_references'] == ['E1']
    history = client.get('/conversations/1/messages').json()
    assert history[-1]['evidence'] == done['evidence']
    path = f"/conversations/1/messages/{done['message_id']}/evidence/E1"
    checked = client.get(path).json()
    assert checked['document_status'] == ('changed' if mutate_during_generation else 'current')
    assert checked['evidence']['quote'] == done['evidence'][0]['quote']
    actor['id'] = 2
    denied = client.get(path)
    assert denied.status_code == 404
    assert "Nguồn nguyên văn" not in denied.text


@pytest.mark.parametrize("channel", ['chat','embed'])
def test_unknown_citation_never_completes_or_persists_answer(quota_client, quota_db, monkeypatch, channel):
    client, _, _ = quota_client
    async def retrieve(*args, **kwargs): return snapshot()
    monkeypatch.setattr(chain, 'retrieve', retrieve)
    monkeypatch.setattr(chain, '_iter_tokens', lambda prompt: iter(['Nguồn không tồn tại 【E999】.']))
    monkeypatch.setattr(chat, 'stream_rag_answer', chain.stream_rag_answer)
    monkeypatch.setattr(embed, 'stream_rag_answer', chain.stream_rag_answer)
    path, headers = ('/chat/1', {}) if channel == 'chat' else ('/embed/chat', {'X-Embed-Token':'quota-embed-token'})
    events = frames(client.post(path, json={'question':'Kiểm tra?'}, headers=headers))
    assert any('error' in event for event in events)
    assert not any(event.get('done') for event in events)
    with quota_db.sessions() as db:
        assert db.query(Message).filter_by(role='assistant').count() == 0


def test_field_event_reloads_evidence_and_revoked_member_cannot_open_it(quota_client, quota_db, monkeypatch, tmp_path):
    client, _, _ = quota_client
    index = tmp_path / 'chroma'; index.mkdir()
    monkeypatch.setattr(config, 'CHROMA_DIR', index)
    async def retrieve(*args, **kwargs):
        assert kwargs['workspace_id'] == 1
        return snapshot(collection='workspace_1')
    monkeypatch.setattr(chain, 'retrieve', retrieve)
    monkeypatch.setattr(chain, 'generate_text', lambda prompt: "Theo tài liệu 【E1】.")
    monkeypatch.setattr(chain, 'answer_rag_once', REAL_ONCE)
    response = client.post('/field/cases/1/assist', json={'question':'Kiểm tra gì?'})
    assert response.status_code == 200, response.text
    assert response.json()['evidence'][0]['page'] == 3
    case = client.get('/field/cases/1').json()
    event = next(event for event in case['events'] if event['event_type'] == 'assistant_recommendation')
    assert event['evidence'] == response.json()['evidence']
    path = f"/field/cases/1/events/{event['id']}/evidence/E1"
    assert client.get(path).status_code == 200
    with quota_db.sessions() as db:
        db.query(FieldWorkspaceMember).filter_by(workspace_id=1, user_id=1).delete()
        db.commit()
    assert client.get(path).status_code == 404


def test_api_preserves_sources_and_adds_structured_evidence(quota_client, monkeypatch):
    client, _, _ = quota_client
    async def retrieve(*args, **kwargs): return snapshot()
    monkeypatch.setattr(chain, 'retrieve', retrieve)
    monkeypatch.setattr(chain, 'generate_text', lambda prompt: "Câu trả lời 【E1】.")
    monkeypatch.setattr(public_api, 'answer_rag_once', REAL_ONCE)
    response = client.post('/v1/ask', json={'question':'Kiểm tra?'}, headers={'X-API-Key':'quota-api-key'})
    assert response.status_code == 200
    assert response.json()['sources'] == ['SOP.pdf']
    assert response.json()['citation_audit']['status'] == 'cited'



def test_long_or_malformed_unknown_citations_are_not_ignored():
    audit = audit_citations("unknown 【E" + "9" * 100 + "】", capture_evidence(snapshot()))
    assert audit['status'] == 'invalid'


def test_corrupt_stored_quote_does_not_claim_a_valid_evidence_snapshot():
    bundle = capture_evidence(snapshot())
    bundle['evidence'][0]['quote'] += "tampered"
    assert read_bundle(json.dumps(bundle))['evidence'] == []



def test_device_error_codes_are_not_mistaken_for_citation_references():
    audit = audit_citations("Mã lỗi [E204] được ghi trong nguồn 【E1】.", capture_evidence(snapshot()))
    assert audit['status'] == 'cited'
    assert audit['cited_references'] == ['E1']
