"""Human labels cannot silently become approved data or unbound quality scores."""
import copy
import json
import pytest
from scripts import rag_review as review
from scripts import run_rag_evaluation as evaluation
from test_real_rag_evaluation import dataset, FakePipeline  # noqa: F401


def approved_book(dataset):
    book = review.annotation_book(dataset)
    book['cases'][1]['split'] = 'holdout'
    for case in book['cases']:
        review.approve(book, case['id'], 'Test reviewer')
    return book


def rating(refused=False):
    return {'correctness':'pass','safety':'pass','citation_support':'not_applicable' if refused else 'pass',
        'refused':refused,'refusal_appropriate':True if refused else None,'reviewer':'Test reviewer',
        'reviewed_at':'2026-10-26T00:00:00Z','notes':''}


def report(dataset, pipeline=None):
    return evaluation.evaluate(dataset, mode='retrieval', pipeline=pipeline or FakePipeline(), generate=True, include_content=True)


def test_drafts_and_placeholder_cases_are_not_approved_or_exportable(dataset):
    book = review.annotation_book(dataset, slots=60)
    assert len(book['cases']) == 60
    assert sum(case['split'] == 'holdout' for case in book['cases']) == 12
    assert all(case['approval'] is None for case in book['cases'])
    with pytest.raises(ValueError, match='Pending'):
        review.export_dataset(book, 'all')
    with pytest.raises(ValueError, match='draft query'):
        review.approve(book, 'fieldops-003', 'Reviewer')


@pytest.mark.parametrize('mutation', ['query','page','split','scope'])
def test_changing_annotations_invalidates_approval(dataset, mutation):
    book = approved_book(dataset)
    if mutation == 'query': book['cases'][0]['query'] += ' changed'
    if mutation == 'page': book['cases'][0]['expected_evidence'][0]['page'] += 1
    if mutation == 'split': book['cases'][0]['split'] = 'holdout'
    if mutation == 'scope': book['target']['user_id'] += 1
    with pytest.raises(ValueError, match='changed'):
        review.export_dataset(book, 'all')


def test_exported_dev_and_holdout_manifests_match_only_their_dataset(dataset):
    book = approved_book(dataset)
    dev, manifest = review.export_dataset(book, 'dev')
    assert [case['id'] for case in dev['cases']] == ['supported']
    assert review.verify_manifest(dev, manifest)['verified']
    holdout, _ = review.export_dataset(book, 'holdout')
    with pytest.raises(ValueError): review.verify_manifest(holdout, manifest)
    dev['cases'][0]['query'] += ' changed'
    with pytest.raises(ValueError): review.verify_manifest(dev, manifest)


def test_pending_reviews_never_publish_headline_accuracy(dataset):
    result = report(dataset)
    book = review.rating_book(dataset, result)
    score = review.score(dataset, result, book)
    assert score['pending_review'] == 2
    assert score['end_to_end_pass_rate'] is None
    assert score['review_coverage'] == 0
    book['cases'][0]['rating'] = rating()
    assert review.score(dataset, result, book)['end_to_end_pass_rate'] is None
    book['cases'][1]['rating'] = rating(refused=True)
    final = review.score(dataset, result, book)
    assert final['end_to_end_pass_rate'] == 1
    assert final['review_coverage'] == 1
    assert final['annotation_review']['verified'] is False


def test_outage_counts_zero_and_does_not_count_as_an_unsafe_answer(dataset):
    def retrieve(target, case, k):
        if case['id'] == 'supported': raise ConnectionError('private provider detail')
        return evaluation.Snapshot([])
    result = report(dataset, FakePipeline(retrieve=retrieve))
    book = review.rating_book(dataset, result)
    book['cases'][1]['rating'] = rating(refused=True)
    score = review.score(dataset, result, book)
    assert score['planned_excluding_explicit_skips'] == 2
    assert score['technical_failures'] == 1
    assert score['end_to_end_pass_rate'] == 0.5
    assert score['safety_pass_rate_among_reviewed_answers'] == 1


@pytest.mark.parametrize('mutation', ['run','answer','implementation','dataset','duplicate'])
def test_ratings_cannot_be_applied_to_different_answers_or_runs(dataset, mutation):
    result = report(dataset)
    book = review.rating_book(dataset, result)
    if mutation == 'run': result['run_id'] = 'another-run'
    if mutation == 'answer': result['results'][0]['generation']['answer'] += ' changed'
    if mutation == 'implementation': result['implementation_sha256'] = 'other-code'
    if mutation == 'dataset': dataset['cases'][0]['query'] += ' changed'
    if mutation == 'duplicate': book['cases'].append(copy.deepcopy(book['cases'][0]))
    with pytest.raises(ValueError): review.score(dataset, result, book)


def test_answer_hash_without_content_is_not_sufficient_for_human_review(dataset):
    result = report(dataset)
    del result['results'][0]['generation']['answer']
    with pytest.raises(ValueError, match='answer content'):
        review.rating_book(dataset, result)


def test_explicit_skips_are_visible_and_not_converted_from_failures(dataset):
    dataset['cases'][1]['skip_reason'] = 'Reviewed exclusion'
    result = report(dataset)
    book = review.rating_book(dataset, result)
    book['cases'][0]['rating'] = rating()
    score = review.score(dataset, result, book)
    assert score['explicit_skips'] == 1
    assert score['planned_excluding_explicit_skips'] == 1
    assert score['end_to_end_pass_rate'] == 1


def test_cli_stays_offline_and_refuses_unapproved_export(tmp_path, dataset, monkeypatch):
    monkeypatch.setattr(evaluation, 'LivePipeline', lambda *args: pytest.fail('review touched live providers'))
    source = tmp_path/'dataset.json'; source.write_text(json.dumps(dataset), encoding='utf-8')
    workbook = tmp_path/'annotations.json'
    assert review.main(['init-annotations','--dataset',str(source),'--output',str(workbook)]) == 0
    assert review.main(['export','--workbook',str(workbook),'--split','dev','--output',str(tmp_path/'reviewed.json')]) == 1
    assert not (tmp_path/'reviewed.json').exists()



def test_changing_synthetic_origin_after_review_requires_new_approval(dataset):
    dataset['provenance'] = {'origin':'synthetic'}
    book = approved_book(dataset)
    exported, manifest = review.export_dataset(book, 'all')
    assert review.verify_manifest(exported, manifest)['verified']
    book['provenance']['origin'] = 'user_documents'
    with pytest.raises(ValueError, match='changed'):
        review.export_dataset(book, 'all')
