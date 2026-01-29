import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from utils import db, data_processor
from ingestion_rapidbus_mrtfeeder import fetch_rapid_rail_live


def show():
    # Refresh behaviour
    if st.session_state.auto_refresh:
        with st.spinner('🛰️ Auto-refreshing...'):
            fetch_rapid_rail_live()
            st.session_state.last_refresh = True
    else:
        # Manual refresh button
        if st.button("🔄 Refresh Data", type="primary"):
            with st.spinner('🛰️ Fetching...'):
                fetch_rapid_rail_live()
                st.session_state.last_refresh = True
            st.rerun()

    # Get historical data (all data, not just latest)
    df_historical, metrics, actual_sync_time = db.get_historical_data()

    if df_historical is None or df_historical.empty:
        st.info("🛰️ No data available. Please refresh.")
        return

    # Convert speed from m/s to km/h, round to whole number, and cap at reasonable max
    df_historical['speed'] = pd.to_numeric(df_historical['speed'], errors='coerce').fillna(0) * 3.6
    df_historical['speed'] = df_historical['speed'].round(0).clip(upper=120)

    # Show sync time
    if actual_sync_time:
        st.success(f"Data updated: {actual_sync_time}")

    # Overview metrics (only Regions Monitored for Analytics)
    col1, col2 = st.columns(2)
    col1.metric("Regions Monitored", metrics['regions'])
    col2.metric("Avg Speed", f"{df_historical['speed'].mean():.2f} km/h")

    st.divider()

    # Charts
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        st.subheader("📊 Buses by Region")
        region_counts = df_historical['region'].value_counts().reset_index()
        region_counts.columns = ['Region', 'Count']

        fig1 = px.bar(
            region_counts,
            x='Count',
            y='Region',
            orientation='h',
            color_discrete_sequence=['#3399FF']  # Single blue color instead of gradient
        )
        if st.session_state.theme_mode == 'dark':
            fig1.update_layout(
                height=400,
                showlegend=False,
                template='plotly_dark',
                paper_bgcolor='#0e1117',
                plot_bgcolor='#0e1117',
                font=dict(color='#fafafa'),
            )
            fig1.update_xaxes(title_font=dict(color='#fafafa'), tickfont=dict(color='#fafafa'))
            fig1.update_yaxes(title_font=dict(color='#fafafa'), tickfont=dict(color='#fafafa'))
        else:
            fig1.update_layout(
                height=400,
                showlegend=False,
                template='plotly_white',
                paper_bgcolor='#f3f4f6',
                plot_bgcolor='#f3f4f6',
                font=dict(color='#111827'),
            )
            fig1.update_xaxes(title_font=dict(color='#111827'), tickfont=dict(color='#111827'))
            fig1.update_yaxes(title_font=dict(color='#111827'), tickfont=dict(color='#111827'))
        st.plotly_chart(fig1, use_container_width=True)

    with col_chart2:
        st.subheader("🏃 Speed Distribution")
        # Filter out zero speeds for cleaner visualization
        speed_data = df_historical[df_historical['speed'] > 0]

        fig2 = px.histogram(
            speed_data,
            x='speed',
            nbins=30,
            labels={'speed': 'Speed (km/h)', 'count': 'Frequency'}
        )
        if st.session_state.theme_mode == 'dark':
            fig2.update_layout(
                height=400,
                showlegend=False,
                template='plotly_dark',
                paper_bgcolor='#0e1117',
                plot_bgcolor='#0e1117',
                font=dict(color='#fafafa'),
            )
            fig2.update_xaxes(title_font=dict(color='#fafafa'), tickfont=dict(color='#fafafa'))
            fig2.update_yaxes(title_font=dict(color='#fafafa'), tickfont=dict(color='#fafafa'))
        else:
            fig2.update_layout(
                height=400,
                showlegend=False,
                template='plotly_white',
                paper_bgcolor='#f3f4f6',
                plot_bgcolor='#f3f4f6',
                font=dict(color='#111827'),
            )
            fig2.update_xaxes(title_font=dict(color='#111827'), tickfont=dict(color='#111827'))
            fig2.update_yaxes(title_font=dict(color='#111827'), tickfont=dict(color='#111827'))
        st.plotly_chart(fig2, use_container_width=True)

    # Pie chart for distribution
    st.subheader("🎯 Regional Distribution")

    fig3 = px.pie(
        region_counts,
        values='Count',
        names='Region',
        hole=0.4
    )
    if st.session_state.theme_mode == 'dark':
        fig3.update_layout(
            height=500,
            template='plotly_dark',
            paper_bgcolor='#0e1117',
            plot_bgcolor='#0e1117',
            font=dict(color='#fafafa'),
        )
    else:
        fig3.update_layout(
            height=500,
            template='plotly_white',
            paper_bgcolor='#f3f4f6',
            plot_bgcolor='#f3f4f6',
            font=dict(color='#111827'),
        )
        # Update pie chart text colors for light theme - both percentage and legend
        fig3.update_traces(textfont=dict(color='#111827'))
        fig3.update_layout(legend=dict(font=dict(color='#111827')))
    st.plotly_chart(fig3, use_container_width=True)

    # Speed by region box plot
    st.subheader("📈 Speed Analysis by Region")

    fig4 = px.box(
        df_historical[df_historical['speed'] > 0],
        x='region',
        y='speed',
        labels={'region': 'Region', 'speed': 'Speed (km/h)'}
    )
    if st.session_state.theme_mode == 'dark':
        fig4.update_layout(
            height=500,
            xaxis_tickangle=-45,
            template='plotly_dark',
            paper_bgcolor='#0e1117',
            plot_bgcolor='#0e1117',
            font=dict(color='#fafafa'),
        )
        fig4.update_xaxes(title_font=dict(color='#fafafa'), tickfont=dict(color='#fafafa'))
        fig4.update_yaxes(title_font=dict(color='#fafafa'), tickfont=dict(color='#fafafa'))
    else:
        fig4.update_layout(
            height=500,
            xaxis_tickangle=-45,
            template='plotly_white',
            paper_bgcolor='#f3f4f6',
            plot_bgcolor='#f3f4f6',
            font=dict(color='#111827'),
        )
        fig4.update_xaxes(title_font=dict(color='#111827'), tickfont=dict(color='#111827'))
        fig4.update_yaxes(title_font=dict(color='#111827'), tickfont=dict(color='#111827'))
    st.plotly_chart(fig4, use_container_width=True)

    # Summary statistics
    st.subheader("📋 Summary Statistics")

    stats_col1, stats_col2, stats_col3 = st.columns(3)

    with stats_col1:
        st.metric("Total Vehicles", len(df_historical))
        st.metric("Moving Vehicles", len(df_historical[df_historical['speed'] > 0]))

    with stats_col2:
        st.metric("Max Speed", f"{df_historical['speed'].max():.2f} km/h")
        st.metric("Min Speed", f"{df_historical['speed'].min():.2f} km/h")

    with stats_col3:
        st.metric("Avg Speed", f"{df_historical['speed'].mean():.2f} km/h")
        st.metric("Median Speed", f"{df_historical['speed'].median():.2f} km/h")

