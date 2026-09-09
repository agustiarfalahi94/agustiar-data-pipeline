import html
import time

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta
from utils import db
from utils import ai_transit
from utils.ingestion import fetch_and_store_transit_data
from utils import background_fetch

try:
    from config import UTC_OFFSET_HOURS
except ImportError:
    UTC_OFFSET_HOURS = 8


def _score_color(score):
    if score >= 80:
        return '#2ecc71'
    elif score >= 50:
        return '#f39c12'
    return '#e74c3c'


def _score_label(score):
    if score >= 80:
        return 'Reliable'
    elif score >= 50:
        return 'Degraded'
    return 'Unreliable'


def _last_vehicles_label(last_vehicle_timestamp, now_ts):
    """
    When this region last reported a bus, phrased for the scorecard.

    The score answers "is the feed working", and an EMPTY cycle — the feed
    answering correctly that nothing is running — is deliberately not counted
    against it. A region can therefore show a green 100 "Reliable" while no
    bus has reported for hours, which reads as "buses are running" and sent a
    reader to the Live Map expecting vehicles. This line is what separates the
    two claims, so the card states the freshness of the buses as well as the
    health of the feed.

    now_ts is passed rather than read from the clock so the wording is
    testable without freezing time.
    """
    if last_vehicle_timestamp is None or pd.isna(last_vehicle_timestamp):
        return 'no buses reported in this window'

    # Clamped: the feed's clock and ours disagree by a few seconds routinely,
    # and "last seen -2 min ago" would be worse than saying nothing.
    seconds = max(0, int(now_ts) - int(last_vehicle_timestamp))
    if seconds < 90:
        return 'buses reporting now'

    minutes = seconds // 60
    if minutes < 60:
        return f'buses last seen {minutes} min ago'

    hours, remainder = divmod(minutes, 60)
    if remainder:
        return f'buses last seen {hours}h {remainder}m ago'
    return f'buses last seen {hours}h ago'


def _sparkline(trend_df, color):
    """
    The 24-hour reliability trace drawn under a region's card.

    The x values are fetch times, not row positions. Plotly substitutes the
    array index when no x is supplied, and that offset reached the reader: a
    hover on this chart read "(1035, 100)", two bare numbers of which the
    first was the 1,036th element of an array and meant nothing outside this
    process. get_region_health_trend already returns a timezone-converted
    `datetime`, so the hover can name the clock time the score was measured
    at instead.

    <extra></extra> suppresses Plotly's trace-name box, which would otherwise
    sit beside the value with nothing useful in it on a single-series chart.

    The y axis stays pinned to 0-100 rather than fitting the data, so a dip to
    60 reads as a dip instead of filling the box — the trace has to stay
    comparable with the headline score printed directly above it.
    """
    if 'datetime' in trend_df.columns:
        x = trend_df['datetime']
        hovertemplate = '%{x|%H:%M} · score %{y:.0f}<extra></extra>'
    else:
        # No timestamps to show. Fall back to the position, but say that is
        # what it is — an unlabelled number here is the original bug.
        x = list(range(len(trend_df)))
        hovertemplate = 'cycle %{x} · score %{y:.0f}<extra></extra>'

    fig = go.Figure(go.Scatter(
        x=x,
        y=trend_df['reliability_score'],
        mode='lines',
        line=dict(color=color, width=1.5),
        hovertemplate=hovertemplate,
    ))
    fig.update_layout(
        height=55,
        margin=dict(l=0, r=0, t=0, b=0),
        showlegend=False,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False, range=[0, 100]),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
    )
    return fig


def _show_ai_panel(health_df, alerts_df):
    """Render an explicit, bounded Gemini query against the current snapshot."""
    st.subheader("🤖 Ask the Network")
    st.caption("Ask about the current network snapshot. Answers are grounded in retrieved DuckDB data and may change after the next refresh.")
    question = st.text_area(
        "Question",
        placeholder="Which regions currently have the lowest reliability?",
        max_chars=ai_transit.MAX_QUESTION_CHARS,
        key="ai_transit_question",
    )
    used = st.session_state.get("ai_transit_queries", 0)
    ask = st.button("Ask Gemini", type="secondary", key="ask_transit_ai")
    if not ask:
        return
    if used >= 5:
        st.warning("This session has reached the five-question limit. Refresh the page later to continue.")
        return
    if not question.strip():
        st.info("Enter a question first.")
        return
    live_df, _, sync_time = db.get_live_data_optimized()
    with st.spinner("Reading the network snapshot..."):
        answer = ai_transit.ask_network(question, health_df, alerts_df, live_df)
    st.session_state.ai_transit_queries = used + 1
    if answer is None:
        st.warning("The AI summary is unavailable right now. Check that GEMINI_API_KEY is configured and try again.")
        return
    st.markdown(answer)
    if sync_time:
        st.caption(f"Grounded in the latest stored vehicle snapshot: {sync_time}.")


def _unavailable_reason(row):
    """Explain an unscored feed without asserting a cause we cannot support.

    `feed_unavailable` only means "no scoreable fetch in the window", which is
    equally true of a withdrawn feed (404) and of one we rate-limited ourselves
    (429). Naming the wrong cause is the exact failure this release exists to
    remove, so the copy follows whichever statuses actually occurred.
    """
    no_feed   = int(row.get('no_feed_count') or 0)
    throttled = int(row.get('throttled_count') or 0)
    if no_feed and throttled:
        return "No feed returned (404) and rate-limited (429) — not scored"
    if no_feed:
        return "Withdrawn upstream (404) — not scored"
    if throttled:
        return "Rate-limited (429), not an agency fault — not scored"
    return "No scoreable fetches in this window — not scored"


def show():
    # Identical refresh pattern to every other page — no special treatment
    if st.session_state.auto_refresh:
        # Behind the page, and once per timer tick rather than once per rerun.
        # Blocking here froze this page for the 3-10 seconds the agency's
        # server takes to answer, and it ran again on every interaction, not
        # only when the 20 seconds were up. See `utils/background_fetch.py`.
        background_fetch.maybe_start_tick_fetch(st.session_state)
    else:
        if st.button("🔄 Refresh Data", type="primary", use_container_width=False):
            with st.spinner('🛰️ Fetching...'):
                fetch_and_store_transit_data()
                st.session_state.last_refresh = True
            st.rerun()

    st.markdown("## 📡 Network Health")
    st.caption("Per-region data quality tracking — how reliably each transit region reports to the API.")

    # The mart bakes in the 24h window, so no window argument is passed.
    health_df = db.get_network_health_summary()

    # Thin-data / no-data notice
    provisional = False
    if health_df.empty:
        st.info("No quality data yet. Click **Refresh Data** or enable auto-refresh to start building history.")
        return
    if health_df['total_fetches'].max() < 10:
        st.info("⚠️ Reliability scores improve with more data. Enable auto-refresh to build history.")
        provisional = True

    # ── Section 1: Network Summary Bar ─────────────────────────────────────
    total_regions = len(health_df)
    if 'feed_unavailable' in health_df.columns:
        unavailable_mask = health_df['feed_unavailable'].fillna(False).astype(bool)
    else:
        unavailable_mask = pd.Series(False, index=health_df.index)
    scored_df  = health_df[~unavailable_mask]
    healthy    = int((scored_df['reliability_score'] >= 80).sum())
    degraded   = int(((scored_df['reliability_score'] >= 50) & (scored_df['reliability_score'] < 80)).sum())
    unreliable = int((scored_df['reliability_score'] < 50).sum())
    unavailable = int(unavailable_mask.sum())
    last_ts    = health_df['last_fetch_timestamp'].max()

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Regions Tracked", total_regions)
    col2.metric("🟢 Reliable",     healthy)
    col3.metric("🟡 Degraded",     degraded)
    col4.metric("🔴 Unreliable",   unreliable)
    # "Not scored" rather than "No Feed": this bucket also holds regions we
    # rate-limited ourselves, which have a feed. Each card names its own cause.
    col6.metric("⚫ Not scored",   unavailable)
    if pd.notna(last_ts):
        dt = datetime.fromtimestamp(int(last_ts), tz=timezone.utc) + timedelta(hours=UTC_OFFSET_HOURS)
        col5.metric("Last Fetch", dt.strftime('%H:%M:%S'))
        st.caption(f"Scores calculated over the last 24h · last fetch at {dt.strftime('%d %b %Y, %H:%M:%S')} (GMT+8)")

    alerts_df = db.get_active_service_alerts()
    if not alerts_df.empty:
        st.warning(f"🚨 **{len(alerts_df)} Active Service Alert(s)** reported across network feeds.")
        with st.expander("View Active Service Alerts Details"):
            for _, alt in alerts_df.iterrows():
                hdr = html.escape(str(alt.get('header_text') or 'Service Alert'))
                desc = html.escape(str(alt.get('description_text') or 'No details provided.'))
                reg = html.escape(str(alt.get('region') or 'Network Wide'))
                cause = html.escape(str(alt.get('cause') or ''))
                effect = html.escape(str(alt.get('effect') or ''))
                st.markdown(f"**[{reg}] {hdr}** ({cause} / {effect})\n\n{desc}")

    _show_ai_panel(health_df, alerts_df)

    st.divider()

    # ── Section 2: Region Scorecards ────────────────────────────────────────
    st.markdown("### Region Reliability Scorecards")
    if provisional:
        st.caption("Provisional — fewer than 10 fetch cycles recorded.")

    regions = health_df.to_dict('records')
    for i in range(0, len(regions), 4):
        cols = st.columns(4)
        for j, row in enumerate(regions[i:i + 4]):
            with cols[j]:
                safe_region = html.escape(str(row['region']))
                if row.get('feed_unavailable'):
                    safe_reason = html.escape(_unavailable_reason(row))
                    st.markdown(f"""
                    <div style="border:1px solid #666;border-radius:8px;padding:12px;margin-bottom:8px;opacity:0.75;">
                        <div style="font-weight:bold;font-size:0.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{safe_region}</div>
                        <div style="font-size:1.1em;color:#999;font-weight:bold;line-height:1.6;">Feed unavailable</div>
                        <div style="font-size:0.75em;color:#999;">{safe_reason}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    continue
                score     = int(row['reliability_score']) if pd.notna(row['reliability_score']) else 0
                color     = _score_color(score)
                label     = _score_label(score)
                reporting = f"{row['reporting_rate'] * 100:.0f}%" if pd.notna(row['reporting_rate']) else "N/A"
                lag       = f"{row['avg_data_lag_seconds']:.0f}s"  if pd.notna(row['avg_data_lag_seconds']) else "N/A"
                # Quiet cycles are context, not a scoring input — an EMPTY fetch
                # is a healthy feed with no service running. Labelled as such so
                # the number can't be read as "the score should be lower".
                quiet     = int(row['dropout_count']) if pd.notna(row['dropout_count']) else 0
                # Separates "the feed is healthy" from "buses are running" —
                # the score only ever claimed the first.
                vehicles  = _last_vehicles_label(row.get('last_vehicle_timestamp'),
                                                 int(time.time()))

                st.markdown(f"""
                <div style="border:1px solid {color};border-radius:8px;padding:12px;margin-bottom:8px;">
                    <div style="font-weight:bold;font-size:0.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{safe_region}</div>
                    <div style="font-size:2em;color:{color};font-weight:bold;line-height:1.1;">{score}</div>
                    <div style="font-size:0.75em;color:{color};">{label}</div>
                    <div style="font-size:0.72em;margin-top:4px;color:#888;">
                        📶 {reporting} &nbsp;|&nbsp; ⏱ {lag}
                    </div>
                    <div style="font-size:0.68em;margin-top:2px;color:#888;" title="Fetch cycles where the feed answered but reported no vehicles. Informational — not part of the score.">
                        💤 {quiet} quiet cycles
                    </div>
                    <div style="font-size:0.68em;margin-top:2px;color:#888;" title="The score measures whether the feed is answering, not whether buses are running. A feed that correctly reports no service scores full marks, so this line says when vehicles were last seen.">
                        🚌 {vehicles}
                    </div>
                </div>
                """, unsafe_allow_html=True)

                t_df = db.get_region_health_trend(row['region'], window_hours=24)
                if not t_df.empty and 'reliability_score' in t_df.columns:
                    st.plotly_chart(
                        _sparkline(t_df, color),
                        use_container_width=True,
                        config={'displayModeBar': False},
                        key=f"sparkline_{row['region']}")

    st.divider()

    # Region selectbox (drives drill-down)
    all_regions = health_df['region'].tolist()
    if st.session_state.get('health_selected_region') not in all_regions:
        st.session_state.health_selected_region = all_regions[0]

    selected = st.selectbox(
        "Select region to inspect",
        options=all_regions,
        index=all_regions.index(st.session_state.health_selected_region),
        key="health_region_selectbox",
    )
    if selected != st.session_state.health_selected_region:
        st.session_state.health_selected_region = selected

    # ── Section 3: Region Drill-Down ────────────────────────────────────────
    st.markdown(f"### 🔍 {selected}")

    window_map = {'1h': 1, '6h': 6, '24h': 24, '7d': 168}
    window_label = st.radio(
        "Time window", list(window_map.keys()), index=2,
        horizontal=True, key="health_window_radio",
    )
    window_hours = window_map[window_label]

    trend_df = db.get_region_health_trend(selected, window_hours=window_hours)

    if trend_df.empty:
        st.info("No data for this region in the selected window.")
    else:
        # Every row unscoreable (NO_FEED/THROTTLED throughout) means there is no
        # score series to draw. A titled, axis-labelled, entirely blank chart
        # reads as a rendering failure, so say why instead.
        if trend_df['reliability_score'].isna().all():
            st.info(
                "Not scored in this window — every fetch was `NO_FEED` (404) or "
                "`THROTTLED` (429), which says nothing about the agency's reliability. "
                "The cycle-level charts below still show what was fetched."
            )
        else:
            fig_score = go.Figure(go.Scatter(
                x=trend_df['datetime'], y=trend_df['reliability_score'],
                mode='lines', name='Reliability Score',
                line=dict(color='#3498db', width=2),
                fill='tozeroy', fillcolor='rgba(52,152,219,0.1)',
            ))
            fig_score.update_layout(
                title='Reliability Score Over Time',
                yaxis=dict(range=[0, 100], title='Score (0–100)'),
                height=280, margin=dict(t=40, b=20),
            )
            st.plotly_chart(fig_score, use_container_width=True, key=f"drill_score_{selected}")

        sample_df = trend_df if len(trend_df) <= 150 else trend_df.iloc[::max(1, len(trend_df) // 150)]
        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(
            x=sample_df['datetime'], y=sample_df['vehicles_inserted'],
            name='Inserted', marker_color='#2ecc71',
        ))
        fig_bar.add_trace(go.Bar(
            x=sample_df['datetime'], y=sample_df['vehicles_rejected'],
            name='Rejected', marker_color='#e74c3c',
        ))
        fig_bar.update_layout(
            title='Vehicles per Fetch Cycle',
            barmode='stack', height=260,
            yaxis=dict(title='Vehicles'),
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_bar, use_container_width=True, key=f"drill_bar_{selected}")

        fig_lag = go.Figure(go.Scatter(
            x=trend_df['datetime'], y=trend_df['avg_data_lag_seconds'],
            mode='lines', name='Avg Lag (s)',
            line=dict(color='#f39c12', width=2),
        ))
        fig_lag.update_layout(
            title='Average Data Lag (seconds)',
            height=230, yaxis=dict(title='Seconds'),
            margin=dict(t=40, b=20),
        )
        st.plotly_chart(fig_lag, use_container_width=True, key=f"drill_lag_{selected}")

    st.divider()

    # ── Section 4: Raw Fetch Log ─────────────────────────────────────────────
    st.markdown("### 📋 Raw Fetch Log")
    log_df = db.get_region_fetch_log(selected, limit=100)

    if log_df.empty:
        st.info("No fetch log entries for this region.")
    else:
        # `fetch_status` is what makes this table forensic rather than merely
        # raw: without it a dropout row shows "Dropout: true" with no reason,
        # contradicting the "Feed unavailable" card above. The CSV export is
        # built from this same frame, so it inherits the column.
        # A database written before the fetch_status migration has no such
        # column until the next fetch runs, so it is included only if present.
        status_cols = ['fetch_status'] if 'fetch_status' in log_df.columns else []
        display_df = log_df[[
            'datetime', *status_cols, 'vehicles_received', 'vehicles_rejected', 'vehicles_inserted',
            'avg_data_lag_seconds', 'max_data_lag_seconds', 'total_dropout', 'fetch_duration_ms',
        ]].rename(columns={
            'datetime':             'Timestamp',
            'fetch_status':         'Status',
            'vehicles_received':    'Received',
            'vehicles_rejected':    'Rejected',
            'vehicles_inserted':    'Inserted',
            'avg_data_lag_seconds': 'Avg Lag (s)',
            'max_data_lag_seconds': 'Max Lag (s)',
            'total_dropout':        'Dropout',
            'fetch_duration_ms':    'Duration (ms)',
        })
        display_df['Avg Lag (s)'] = display_df['Avg Lag (s)'].round(1)
        display_df['Max Lag (s)'] = display_df['Max Lag (s)'].round(1)

        st.dataframe(display_df, use_container_width=True)

        csv = display_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="⬇️ Export CSV",
            data=csv,
            file_name=f"fetch_log_{selected.replace(' ', '_')}.csv",
            mime="text/csv",
            key="health_export_csv",
        )
