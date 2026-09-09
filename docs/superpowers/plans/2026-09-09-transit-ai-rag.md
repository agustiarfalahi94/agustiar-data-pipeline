# Transit AI RAG Implementation Plan

> **For agentic workers:** Implement this plan task-by-task with test-first development.

**Goal:** Add a safe “Ask the Network” Gemini feature that answers questions from current transit data.

**Architecture:** Streamlit retrieves bounded network-health, live-vehicle and service-alert context from DuckDB. A server-side Gemini request summarizes only that context after an explicit Enter submission. Pending work and completed results live in Streamlit session state, and the next automatic-refresh timer is armed only after pending AI work finishes. The feature degrades cleanly when no key or live data is available.

**Tech Stack:** Python, Streamlit, DuckDB, pandas, requests, Gemini `generateContent` REST API, pytest.

**Spec:** `docs/superpowers/plans/2026-09-09-transit-ai-rag.md`

## Global Constraints

- `GEMINI_API_KEY` is read only from environment variables or Streamlit Secrets.
- The browser never receives the API key.
- Retrieval is deterministic SQL/dataframe context, not an unsupported vector-search claim.
- Answers must distinguish live feed facts from estimates and say when context is missing.
- Automatic 20-second refresh must not trigger or duplicate Gemini calls, and
  must not erase the question or answer.

### Task 1: Grounded Gemini service

**Files:**
- Create: `src/utils/ai_transit.py`
- Test: `tests/test_ai_transit.py`

- [x] Write failing tests for bounded context, missing-key fallback, valid Gemini response parsing, and HTTP errors.
- [x] Implement `build_transit_context`, `ask_network`, and `answer_from_response` with injected HTTP transport.
- [x] Run `pytest tests/test_ai_transit.py -v` and the full suite.

### Task 2: Streamlit interface

**Files:**
- Modify: `src/app.py`
- Modify: `src/app_pages/network_health.py`

- [x] Add a global Ask the Network panel to the shared sidebar.
- [x] Retrieve page-specific DB summaries, alerts and live data, apply a per-session request limit, and call Gemini only on submit.
- [x] Show source timestamp/context limitations and graceful no-key/no-data messages.
- [x] Submit with Enter, persist pending/completed conversation state across
  timed reruns, and stop before Gemini when no live data has been fetched.
- [x] Include deterministic unique-vehicle counts by region and display only
  complete non-thinking Gemini response parts.
- [x] Run the full test suite.

### Task 3: Documentation and release

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [x] Document the RAG data flow, `GEMINI_API_KEY`, limits and examples.
- [x] Run formatting/static checks and commit the feature.
- [x] Push the branch, open a PR, and merge it after checks pass.
