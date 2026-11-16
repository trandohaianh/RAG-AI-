"""The sample corpus stays explicitly synthetic, unapproved and reproducible."""
import json
from pathlib import Path
import pytest
from scripts import run_eval_benchmark as lexical
from scripts import run_rag_evaluation as evaluation
from scripts import rag_review

BASE = Path(__file__).resolve().parents[1] / 'examples' / 'fieldops-sop-demo'


def test_sample_manuals_match_the_offline_fixture_and_annotations():
    fixture = lexical.load_dataset(BASE/'lexical-benchmark.json')
    dataset = evaluation.load_dataset(BASE/'questions.template.json')
    book = rag_review.read(BASE/'annotations.draft.json')
    assert len(fixture['documents']) == 8
    assert len(dataset['cases']) == len(book['cases']) == 60
    assert dataset['kind'] == 'template'
    assert dataset['provenance']['origin'] == book['provenance']['origin'] == 'synthetic'
    assert all(case['approval'] is None for case in book['cases'])
    assert sum(case['split'] == 'holdout' for case in book['cases']) == 12
    for doc in fixture['documents']:
        assert (BASE/doc['source']).read_text(encoding='utf-8') == doc['text']
        assert doc['page'] is None
        assert 'GIẢ LẬP' in doc['text']
    with pytest.raises(ValueError): rag_review.export_dataset(book, 'all')


def test_synthetic_lexical_run_is_not_generated_answer_accuracy():
    report = lexical.run_offline(BASE/'lexical-benchmark.json')
    assert report['completed'] == 60 and report['failed'] == 0
    assert report['generation']['status'] == 'not_evaluated'
    assert report['generation']['refusal_rate'] is None
    assert report['scope'] == 'synthetic BM25-only retrieval'


def test_boolean_page_is_not_accepted_as_a_real_page(tmp_path):
    data = json.loads((BASE/'lexical-benchmark.json').read_text(encoding='utf-8'))
    data['documents'][0]['page'] = True
    path = tmp_path/'bad.json'; path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError): lexical.load_dataset(path)
