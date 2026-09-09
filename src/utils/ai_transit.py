"""Grounded Gemini summaries for the Malaysia transit dashboard."""

import os
from typing import Any, Callable, Optional

import pandas as pd
import requests

MAX_QUESTION_CHARS = 500
MAX_CONTEXT_CHARS = 12_000
MAX_OUTPUT_TOKENS = 350
DEFAULT_MODEL = "gemini-3.6-flash"


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
        parts = payload["candidates"][0]["content"]["parts"]
        text = parts[0]["text"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(text, str):
        return None
    text = text.strip()
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
        "and estimates. If the data does not answer the question, say so. Keep "
        "the answer under 120 words and mention that the snapshot may change.\n\n"
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
                "generationConfig": {"temperature": 0.2, "maxOutputTokens": MAX_OUTPUT_TOKENS},
            },
            timeout=20,
        )
        if response.status_code != 200:
            return None
        return answer_from_response(response.json())
    except (requests.RequestException, ValueError, AttributeError):
        return None
