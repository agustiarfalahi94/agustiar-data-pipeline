import runpy
import sys
import types
from pathlib import Path

import pandas as pd

from utils import ai_transit


APP_PATH = Path(__file__).parents[1] / "src" / "app.py"


class SessionState(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc

    def __setattr__(self, key, value):
        self[key] = value


class StreamlitStub(types.ModuleType):
    def __init__(self, state, submitted_question=None):
        super().__init__("streamlit")
        self.session_state = SessionState(state)
        self.sidebar = self
        self.submitted_question = submitted_question
        self.chat_input_calls = 0
        self.markdown_calls = []
        self.errors = []
        self.warnings = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def set_page_config(self, **kwargs):
        return None

    def subheader(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def divider(self):
        return None

    def title(self, *args, **kwargs):
        return None

    def write(self, value, **kwargs):
        self.markdown_calls.append(str(value))

    def markdown(self, value, **kwargs):
        self.markdown_calls.append(str(value))

    def error(self, value, **kwargs):
        self.errors.append(str(value))

    def warning(self, value, **kwargs):
        self.warnings.append(str(value))

    def info(self, *args, **kwargs):
        return None

    def radio(self, label, options, **kwargs):
        if label == "Mode":
            return "Auto (20s)" if self.session_state.get("auto_refresh") else "Manual"
        return self.session_state.get("current_page", options[0])

    def button(self, label, **kwargs):
        return label == "Ask Gemini" and self.submitted_question is not None

    def text_area(self, *args, **kwargs):
        return self.submitted_question or ""

    def chat_input(self, *args, key=None, on_submit=None, **kwargs):
        self.chat_input_calls += 1
        if self.submitted_question is not None:
            self.session_state[key] = self.submitted_question
            if on_submit:
                on_submit()
        return self.submitted_question

    def spinner(self, *args, **kwargs):
        return self

    def rerun(self):
        raise AssertionError("Unexpected rerun in app-level AI test")


def run_app(monkeypatch, *, state=None, submitted_question=None, live=None):
    state = state or {}
    live = pd.DataFrame() if live is None else live
    st = StreamlitStub(state, submitted_question)
    refresh_calls = []

    refresh_module = types.ModuleType("streamlit_autorefresh")
    refresh_module.st_autorefresh = lambda **kwargs: refresh_calls.append({
        "kwargs": kwargs,
        "answer_when_armed": st.session_state.get("ai_last_answer"),
    })

    database = types.SimpleNamespace(
        get_network_health_summary=lambda: pd.DataFrame(),
        get_active_service_alerts=lambda: pd.DataFrame(),
        get_live_data_optimized=lambda: (live, {}, "9 Sep 2026, 19:30"),
    )
    utils_module = types.ModuleType("utils")
    utils_module.ai_transit = ai_transit
    utils_module.db = database

    live_map = types.SimpleNamespace(show=lambda: None)
    app_pages = types.ModuleType("app_pages")
    app_pages.live_map = live_map

    monkeypatch.setitem(sys.modules, "streamlit", st)
    monkeypatch.setitem(sys.modules, "streamlit_autorefresh", refresh_module)
    monkeypatch.setitem(sys.modules, "utils", utils_module)
    monkeypatch.setitem(sys.modules, "app_pages", app_pages)
    monkeypatch.setattr(ai_transit, "ask_network", lambda *args, **kwargs: "**Busiest:** Rapid Bus KL")

    runpy.run_path(str(APP_PATH), run_name="test_transit_app")
    return st, refresh_calls


def test_ai_question_uses_enter_submitting_chat_input(monkeypatch):
    st, _ = run_app(monkeypatch)

    assert st.chat_input_calls == 1


def test_ai_question_stops_before_gemini_when_no_live_data_exists(monkeypatch):
    st, _ = run_app(
        monkeypatch,
        submitted_question="Which region is busiest?",
        live=pd.DataFrame(),
    )

    assert st.errors == [ai_transit.NO_DATA_MESSAGE]


def test_saved_ai_answer_is_rendered_again_after_a_rerun(monkeypatch):
    st, _ = run_app(
        monkeypatch,
        state={
            "ai_last_question": "Which region is busiest?",
            "ai_last_answer": "**Busiest:** Rapid Bus KL",
            "ai_last_error": None,
            "ai_last_sync_time": "9 Sep 2026, 19:30",
        },
    )

    assert "Which region is busiest?" in st.markdown_calls
    assert "**Busiest:** Rapid Bus KL" in st.markdown_calls


def test_pending_ai_question_is_answered_before_autorefresh_is_rearmed(monkeypatch):
    st, refresh_calls = run_app(
        monkeypatch,
        state={
            "auto_refresh": True,
            "ai_pending_question": "Which region is busiest?",
            "ai_last_question": "Which region is busiest?",
        },
        live=pd.DataFrame([{"region": "Rapid Bus KL", "vehicle_id": "V1"}]),
    )

    assert refresh_calls[0]["answer_when_armed"] == "**Busiest:** Rapid Bus KL"
    assert st.session_state["ai_last_answer"] == "**Busiest:** Rapid Bus KL"
    assert "ai_pending_question" not in st.session_state
