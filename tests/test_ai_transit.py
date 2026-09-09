import pandas as pd

from utils.ai_transit import (
    MAX_QUESTION_CHARS,
    answer_from_response,
    build_transit_context,
)


def test_context_contains_retrieved_facts_and_not_unbounded_rows():
    health = pd.DataFrame([{
        'region': 'Rapid Bus KL',
        'reliability_score': 92,
        'total_fetches': 40,
        'last_fetch_timestamp': 1700000000,
    }])
    alerts = pd.DataFrame([{
        'region': 'Rapid Bus KL',
        'header_text': 'Road closure',
        'description_text': 'Diversion near Awan Besar',
    }])
    live = pd.DataFrame([{'region': 'Rapid Bus KL', 'vehicle_id': 'V1'}])

    context = build_transit_context(health, alerts, live)

    assert 'Rapid Bus KL' in context
    assert 'Road closure' in context
    assert 'V1' in context
    assert len(context) < 12_000


def test_answer_from_response_extracts_gemini_text():
    payload = {
        'candidates': [{'content': {'parts': [{'text': 'Rapid Bus KL is reliable.'}]}}]
    }
    assert answer_from_response(payload) == 'Rapid Bus KL is reliable.'


def test_answer_from_response_rejects_empty_or_malformed_response():
    assert answer_from_response({}) is None
    assert answer_from_response({'candidates': [{'content': {'parts': []}}]}) is None


def test_question_limit_is_explicit():
    assert MAX_QUESTION_CHARS == 500


def test_context_includes_page_specific_retrieval_sections():
    context = build_transit_context(
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        extra_sections={"ANALYTICS SNAPSHOT": "Rapid Bus Penang: 42 vehicles"},
    )
    assert "ANALYTICS SNAPSHOT" in context
    assert "Rapid Bus Penang: 42 vehicles" in context
