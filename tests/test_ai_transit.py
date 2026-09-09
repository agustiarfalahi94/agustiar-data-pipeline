import pandas as pd

from utils import ai_transit
from utils.ai_transit import (
    MAX_QUESTION_CHARS,
    answer_from_response,
    ask_network,
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


def test_context_includes_current_unique_vehicle_counts_by_region():
    live = pd.DataFrame([
        {'region': 'Rapid Bus KL', 'vehicle_id': 'V1'},
        {'region': 'Rapid Bus KL', 'vehicle_id': 'V1'},
        {'region': 'Rapid Bus KL', 'vehicle_id': 'V2'},
        {'region': 'myBAS Kuala Terengganu', 'vehicle_id': 'V3'},
    ])

    context = build_transit_context(
        pd.DataFrame(),
        pd.DataFrame(),
        live,
    )

    assert 'CURRENT LIVE VEHICLE COUNTS BY REGION' in context
    assert '{"region":"Rapid Bus KL","vehicle_count":2}' in context
    assert '{"region":"myBAS Kuala Terengganu","vehicle_count":1}' in context


def test_answer_from_response_extracts_gemini_text():
    payload = {
        'candidates': [{'content': {'parts': [{'text': 'Rapid Bus KL is reliable.'}]}}]
    }
    assert answer_from_response(payload) == 'Rapid Bus KL is reliable.'


def test_answer_from_response_rejects_empty_or_malformed_response():
    assert answer_from_response({}) is None
    assert answer_from_response({'candidates': [{'content': {'parts': []}}]}) is None


def test_answer_from_response_ignores_thoughts_and_joins_final_text_parts():
    payload = {
        'candidates': [{
            'finishReason': 'STOP',
            'content': {'parts': [
                {'thought': True, 'text': ') * myBAS Kuala Tereng'},
                {'text': '**Busiest region:** '},
                {'text': 'myBAS Kuala Terengganu'},
            ]},
        }]
    }

    assert answer_from_response(payload) == (
        '**Busiest region:** myBAS Kuala Terengganu'
    )


def test_answer_from_response_rejects_token_truncated_candidates():
    payload = {
        'candidates': [{
            'finishReason': 'MAX_TOKENS',
            'content': {'parts': [{'text': ') * myBAS Kuala Tereng'}]},
        }]
    }

    assert answer_from_response(payload) is None


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


def test_ask_network_uses_supported_gemini_flash_model():
    request = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "candidates": [{"content": {"parts": [{"text": "Service is operating."}]}}]
            }

    def post(url, **kwargs):
        request["url"] = url
        return Response()

    answer = ask_network(
        "How is the network?",
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        api_key="test-key",
        http_post=post,
    )

    assert answer == "Service is operating."
    assert request["url"].endswith("/gemini-3.6-flash:generateContent")


def test_ask_network_requests_complete_low_thinking_answers():
    request = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "candidates": [{
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "Service is operating."}]},
                }]
            }

    def post(url, **kwargs):
        request.update(kwargs)
        return Response()

    assert ask_network(
        "How is the network?",
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
        api_key="test-key",
        http_post=post,
    ) == "Service is operating."

    config = request["json"]["generationConfig"]
    assert config["maxOutputTokens"] == 1_024
    assert config["thinkingConfig"] == {"thinkingLevel": "low"}
    prompt = request["json"]["contents"][0]["parts"][0]["text"]
    assert "clean Markdown" in prompt
    assert "complete sentence" in prompt


def test_queue_question_keeps_pending_work_across_reruns():
    state = {
        "ai_last_answer": "Old answer",
        "ai_last_error": "Old error",
    }

    ai_transit.queue_question(state, "  Which region is busiest?  ")
    ai_transit.initialise_conversation(state)

    assert state["ai_pending_question"] == "Which region is busiest?"
    assert state["ai_last_question"] == "Which region is busiest?"
    assert state["ai_last_answer"] is None
    assert state["ai_last_error"] is None


def test_complete_question_persists_answer_and_clears_pending_work():
    state = {"ai_pending_question": "Which region is busiest?"}

    ai_transit.complete_question(
        state,
        answer="**Busiest region:** Rapid Bus KL",
        sync_time="9 Sep 2026, 19:30",
    )
    ai_transit.initialise_conversation(state)

    assert "ai_pending_question" not in state
    assert state["ai_last_answer"] == "**Busiest region:** Rapid Bus KL"
    assert state["ai_last_sync_time"] == "9 Sep 2026, 19:30"


def test_live_data_available_requires_at_least_one_vehicle_row():
    assert not ai_transit.live_data_available(None)
    assert not ai_transit.live_data_available(pd.DataFrame())
    assert ai_transit.live_data_available(pd.DataFrame([{"vehicle_id": "V1"}]))
