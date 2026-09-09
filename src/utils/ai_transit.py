"""Grounded Gemini summaries for the Malaysia transit dashboard."""

import os
from typing import Any, Callable, MutableMapping, Optional

import pandas as pd
import requests

MAX_QUESTION_CHARS = 500
MAX_CONTEXT_CHARS = 12_000
MAX_OUTPUT_TOKENS = 1_024
DEFAULT_MODEL = "gemini-3.6-flash"
NO_DATA_MESSAGE = "No data fetched yet. Start fetching data first!"

_CONVERSATION_DEFAULTS = {
    "ai_last_question": None,
    "ai_last_answer": None,
    "ai_last_error": None,
    "ai_last_sync_time": None,
}


def initialise_conversation(state: MutableMapping[str, Any]) -> None:
    """Add missing AI conversation keys without replacing saved results."""
    for key, value in _CONVERSATION_DEFAULTS.items():
        state.setdefault(key, value)


def queue_question(state: MutableMapping[str, Any], question: str) -> None:
    """Persist submitted work before Streamlit begins the next rerun."""
    initialise_conversation(state)
    question = str(question or "").strip()
    if not question:
        return
    state["ai_pending_question"] = question
    state["ai_last_question"] = question
    state["ai_last_answer"] = None
    state["ai_last_error"] = None
    state["ai_last_sync_time"] = None


def complete_question(
    state: MutableMapping[str, Any],
    *,
    answer: Optional[str] = None,
    error: Optional[str] = None,
    sync_time: Optional[str] = None,
) -> None:
    """Store the visible result so ordinary and timed reruns can render it."""
    initialise_conversation(state)
    state["ai_last_answer"] = answer
    state["ai_last_error"] = error
    state["ai_last_sync_time"] = sync_time
    state.pop("ai_pending_question", None)


def live_data_available(live: Optional[pd.DataFrame]) -> bool:
    """Return whether a fetched live-vehicle snapshot can ground an answer."""
    return live is not None and not live.empty


def build_transit_context(
    health: pd.DataFrame,
    alerts: pd.DataFrame,
    live: Optional[pd.DataFrame],
    extra_sections: Optional[dict[str, str]] = None,
) -> str:
    """Serialize a bounded snapshot of retrieved transit facts."""
    sections = []
    if health is not None and not health.empty:
        sections.append("NETWORK HEALTH:\n" + health.head(30).to_json(orient="records", date_format="iso"))
    if alerts is not None and not alerts.empty:
        sections.append("ACTIVE SERVICE ALERTS:\n" + alerts.head(30).to_json(orient="records", date_format="iso"))
    if live is not None and not live.empty:
        if {"region", "vehicle_id"}.issubset(live.columns):
            counts = (
                live.dropna(subset=["region", "vehicle_id"])
                .groupby("region")["vehicle_id"]
                .nunique()
                .sort_values(ascending=False)
                .rename("vehicle_count")
                .reset_index()
            )
            if not counts.empty:
                sections.append(
                    "CURRENT LIVE VEHICLE COUNTS BY REGION:\n"
                    + counts.to_json(orient="records")
                )
        sections.append("LIVE VEHICLE SNAPSHOT:\n" + live.head(100).to_json(orient="records", date_format="iso"))
    for title, value in (extra_sections or {}).items():
        if value:
            sections.append(f"{title}:\n{value}")
    if not sections:
        return "NO RETRIEVED DATA IS AVAILABLE."
    return "\n\n".join(sections)[:MAX_CONTEXT_CHARS]


def answer_from_response(payload: dict[str, Any]) -> Optional[str]:
    """Extract Gemini text without trusting arbitrary response shapes."""
    try:
        candidate = payload["candidates"][0]
        if candidate.get("finishReason") not in (None, "STOP"):
            return None
        parts = candidate["content"]["parts"]
    except (KeyError, IndexError, TypeError):
        return None

    text = "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and not part.get("thought", False)
        and isinstance(part.get("text"), str)
    ).strip()
    return text[:2_000] or None


def configured_api_key() -> Optional[str]:
    """Resolve the server-side key without exposing it to the browser."""
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        key = str(__import__("streamlit").secrets["GEMINI_API_KEY"])
    except Exception:
        return None
    return key or None


def ask_network(
    question: str,
    health: pd.DataFrame,
    alerts: pd.DataFrame,
    live: Optional[pd.DataFrame],
    *,
    api_key: Optional[str] = None,
    http_post: Callable[..., Any] = requests.post,
    model: Optional[str] = None,
    extra_sections: Optional[dict[str, str]] = None,
) -> Optional[str]:
    """Ask Gemini to summarize only the retrieved transit snapshot."""
    question = question.strip()
    if not question or len(question) > MAX_QUESTION_CHARS:
        return None
    key = api_key or configured_api_key()
    if not key:
        return None

    context = build_transit_context(health, alerts, live, extra_sections=extra_sections)
    prompt = (
        "You are a transit operations assistant for Malaysia. Answer the user "
        "using only the retrieved data below. Do not invent routes, times, "
        "causes, or locations. Distinguish live vehicle facts, timetable facts, "
        "and estimates. If the data does not answer the question, say so. Return "
        "clean Markdown that begins with a complete sentence; use bullets only "
        "when comparing multiple items. Keep the answer under 120 words and "
        "mention that the snapshot may change.\n\n"
        f"RETRIEVED DATA:\n{context}\n\nUSER QUESTION:\n{question}"
    )
    endpoint = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model or DEFAULT_MODEL}:generateContent"
    )
    try:
        response = http_post(
            endpoint,
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": MAX_OUTPUT_TOKENS,
                    "thinkingConfig": {"thinkingLevel": "low"},
                },
            },
            timeout=20,
        )
        if response.status_code != 200:
            return None
        return answer_from_response(response.json())
    except (requests.RequestException, ValueError, AttributeError):
        return None
