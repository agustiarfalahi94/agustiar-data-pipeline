# Transit AI RAG Implementation Plan

> **For agentic workers:** Implement this plan task-by-task with test-first development.

**Goal:** Add a safe “Ask the Network” Gemini feature that answers questions from current transit data.

**Architecture:** Streamlit retrieves bounded network-health, live-vehicle and service-alert context from DuckDB. A server-side Gemini request summarizes only that context; the feature never runs during automatic refresh and degrades cleanly when no key or data is available.

**Tech Stack:** Python, Streamlit, DuckDB, pandas, requests, Gemini `generateContent` REST API, pytest.

**Spec:** `docs/superpowers/plans/2026-09-09-transit-ai-rag.md`

## Global Constraints

- `GEMINI_API_KEY` is read only from environment variables or Streamlit Secrets.
- The browser never receives the API key.
- Retrieval is deterministic SQL/dataframe context, not an unsupported vector-search claim.
- Answers must distinguish live feed facts from estimates and say when context is missing.
- Automatic 20-second refresh must not trigger Gemini calls.

### Task 1: Grounded Gemini service

**Files:**
- Create: `src/utils/ai_transit.py`
- Test: `tests/test_ai_transit.py`

- [ ] Write failing tests for bounded context, missing-key fallback, valid Gemini response parsing, and HTTP errors.
- [ ] Implement `build_transit_context`, `ask_network`, and `answer_from_response` with injected HTTP transport.
- [ ] Run `pytest tests/test_ai_transit.py -v` and the full suite.

### Task 2: Streamlit interface

**Files:**
- Modify: `src/app.py`
- Modify: `src/app_pages/network_health.py`

- [ ] Add an explicit Ask the Network panel to Network Health.
- [ ] Retrieve existing DB summaries and alerts, apply a per-session request limit, and call Gemini only on submit.
- [ ] Show source timestamp/context limitations and graceful no-key/no-data messages.
- [ ] Run the full test suite.

### Task 3: Documentation and release

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`

- [ ] Document the RAG data flow, `GEMINI_API_KEY`, limits and examples.
- [ ] Run formatting/static checks and commit the feature.
- [ ] Push the branch, open a PR, and merge it after checks pass.
