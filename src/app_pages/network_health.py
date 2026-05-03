import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import duckdb
from datetime import datetime, timezone, timedelta
from utils import db
from utils.ingestion import fetch_and_store_transit_data

try:
    from config import UTC_OFFSET_HOURS, DATABASE_NAME
except ImportError:
    UTC_OFFSET_HOURS = 8
    DATABASE_NAME = 'agustiar_analytics.duckdb'


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


def show():
    # Identical refresh pattern to every other page — no special treatment
    if st.session_state.auto_refresh:
        with st.spinner('🛰️ Auto-refreshing...'):
            fetch_and_store_transit_data()
            st.session_state.last_refresh = True
    else:
        if st.button("🔄 Refresh Data", type="primary", use_container_width=False):
            with st.spinner('🛰️ Fetching...'):
                fetch_and_store_transit_data()
                st.session_state.last_refresh = True
            st.rerun()

    st.markdown("## 📡 Network Health")
    st.caption("Per-region data quality tracking — how reliably each transit region reports to the API.")

    # ── Debug panel (remove once issue is resolved) ──────────────────────────
    with st.expander("🔍 Debug — DB state", expanded=True):
        try:
            _con = duckdb.connect(DATABASE_NAME)
            try:
                tables = _con.execute(
                    "SELECT table_name FROM information_schema.tables ORDER BY table_name"
                ).df()
                st.write("**Tables in DB:**", tables['table_name'].tolist())

                quality_exists = 'fetch_quality_log' in tables['table_name'].tolist()
                st.write("**fetch_quality_log exists:**", quality_exists)

                if quality_exists:
                    row_count = _con.execute("SELECT COUNT(*) FROM fetch_quality_log").fetchone()[0]
                    st.write("**fetch_quality_log row count:**", row_count)
                    if row_count > 0:
                        latest = _con.execute(
                            "SELECT * FROM fetch_quality_log ORDER BY fetch_timestamp DESC LIMIT 5"
                        ).df()
                        st.dataframe(latest)
                    else:
                        st.warning("Table exists but has 0 rows.")

                live_count = _con.execute("SELECT COUNT(*) FROM live_buses").fetchone()[0]
                st.write("**live_buses row count:**", live_count)
            finally:
                _con.close()
        except Exception as e:
            st.error(f"Debug query failed: {e}")
    # ── End debug panel ───────────────────────────────────────────────────────

    health_df = db.get_network_health_summary(window_hours=24)

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
    healthy    = int((health_df['reliability_score'] >= 80).sum())
    degraded   = int(((health_df['reliability_score'] >= 50) & (health_df['reliability_score'] < 80)).sum())
    unreliable = int((health_df['reliability_score'] < 50).sum())
    last_ts    = health_df['last_fetch_timestamp'].max()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Regions Tracked", total_regions)
    col2.metric("🟢 Reliable",     healthy)
    col3.metric("🟡 Degraded",     degraded)
    col4.metric("🔴 Unreliable",   unreliable)
    if pd.notna(last_ts):
        dt = datetime.fromtimestamp(int(last_ts), tz=timezone.utc) + timedelta(hours=UTC_OFFSET_HOURS)
        col5.metric("Last Fetch", dt.strftime('%H:%M:%S'))
        st.caption(f"Scores calculated over the last 24h · last fetch at {dt.strftime('%d %b %Y, %H:%M:%S')} (GMT+8)")

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
                score     = int(row['reliability_score']) if pd.notna(row['reliability_score']) else 0
                color     = _score_color(score)
                label     = _score_label(score)
                reporting = f"{row['reporting_rate'] * 100:.0f}%" if pd.notna(row['reporting_rate']) else "N/A"
                lag       = f"{row['avg_data_lag_seconds']:.0f}s"  if pd.notna(row['avg_data_lag_seconds']) else "N/A"
                dropouts  = int(row['dropout_count']) if pd.notna(row['dropout_count']) else 0

                st.markdown(f"""
                <div style="border:1px solid {color};border-radius:8px;padding:12px;margin-bottom:8px;">
                    <div style="font-weight:bold;font-size:0.85em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{row['region']}</div>
                    <div style="font-size:2em;color:{color};font-weight:bold;line-height:1.1;">{score}</div>
                    <div style="font-size:0.75em;color:{color};">{label}</div>
                    <div style="font-size:0.72em;margin-top:4px;color:#888;">
                        📶 {reporting} &nbsp;|&nbsp; ⏱ {lag} &nbsp;|&nbsp; 🚫 {dropouts}
                    </div>
                </div>
                """, unsafe_allow_html=True)

                t_df = db.get_region_health_trend(row['region'], window_hours=24)
                if not t_df.empty and 'reliability_score' in t_df.columns:
                    fig = go.Figure(go.Scatter(
                        y=t_df['reliability_score'],
                        mode='lines',
                        line=dict(color=color, width=1.5),
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
                    st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False},
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
        display_df = log_df[[
            'datetime', 'vehicles_received', 'vehicles_rejected', 'vehicles_inserted',
            'avg_data_lag_seconds', 'max_data_lag_seconds', 'total_dropout', 'fetch_duration_ms',
        ]].rename(columns={
            'datetime':             'Timestamp',
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
