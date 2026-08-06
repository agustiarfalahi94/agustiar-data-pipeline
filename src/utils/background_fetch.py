"""Run the feed fetch off the render path.

A fetch is 3-10 seconds of waiting on api.data.gov.my — the server takes 3-6
seconds to answer a single endpoint, even one whose whole reply is 143 bytes.
Reading the result back out of DuckDB takes about 30 milliseconds. So the page
was never slow; it was standing still in front of a network call, and the
20-second auto-refresh froze it for the length of the slowest agency's reply.

The fetch now runs on a worker thread and the page renders from whatever the
database already holds. New rows appear on the following tick, which is a small
cost against data that is already 26-124 seconds old when it arrives.

The thread must not touch `st.*`. It has no ScriptRunContext, and Streamlit's
session state belongs to one session while this thread is shared by all of
them. It does HTTP and DuckDB only, and reports back through `is_running()` —
a plain bool the page can read — rather than by writing anything Streamlit owns.
"""

import threading

_LOCK = threading.Lock()
_THREAD = None


def start():
    """Begin a fetch unless one is already running.

    Returns True when this call started one, False when a fetch was already in
    flight. Never blocks: a slow fetch must not pile a second one on top of
    itself on the next tick, or a feed having a bad minute would turn into a
    thread per tick, all writing to the same database.
    """
    global _THREAD
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return False
        _THREAD = threading.Thread(
            target=_run, name='transit-fetch', daemon=True)
        _THREAD.start()
        return True


def is_running():
    """True while a fetch is in flight. The page shows its 'updating' note off this."""
    with _LOCK:
        return _THREAD is not None and _THREAD.is_alive()


def maybe_start_tick_fetch(session_state):
    """Start a fetch once per auto-refresh tick, not once per rerun.

    `st_autorefresh` counts its own ticks into session state, so comparing that
    count against the last one fetched separates "20 seconds passed" from "the
    user tapped something". Without it every interaction refetched the whole
    network.

    Returns True when this call started a fetch.
    """
    if not session_state.get('auto_refresh'):
        return False
    tick = session_state.get('auto_refresh_counter')
    if tick == session_state.get('auto_refresh_fetched_tick'):
        return False
    session_state['auto_refresh_fetched_tick'] = tick
    return start()


def _run():
    # Imported here rather than at module load so a test that patches
    # `utils.ingestion.fetch_and_store_transit_data` is the function this
    # thread actually calls.
    from utils.ingestion import fetch_and_store_transit_data
    try:
        fetch_and_store_transit_data()
    except Exception as exc:
        # A failed fetch must not kill the thread's process or surface as a
        # traceback in the browser. The page already reports how old its data
        # is, which is the honest signal whether a fetch failed or the agency
        # simply had nothing to say.
        print(f"Background fetch failed: {exc}")
