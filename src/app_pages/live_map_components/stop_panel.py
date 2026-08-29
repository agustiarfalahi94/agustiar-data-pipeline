"""
stop_panel.py
--------------
Nearby stops panel and arrival board rendering for Live Map.
"""

import html
import streamlit as st


def render_stop_arrival_row(arrival):
    """
    Render a single stop arrival entry row.
    """
    route_disp = html.escape(str(arrival.get('route_display') or 'Route'))
    headsign = html.escape(str(arrival.get('headsign') or ''))
    eta_sec = arrival.get('eta_seconds')
    delay_sec = arrival.get('delay_seconds')
    age_sec = arrival.get('age_seconds', 0)

    if eta_sec is None or eta_sec < 0:
        eta_str = "arriving now"
    else:
        eta_min = int(round(eta_sec / 60))
        eta_str = f"arrives ~{eta_min} min"

    delay_str = ""
    if delay_sec is not None:
        if delay_sec > 60:
            delay_str = f" · {int(round(delay_sec/60))} min late"
        elif delay_sec < -60:
            delay_str = f" · {int(round(abs(delay_sec)/60))} min early"
        else:
            delay_str = " · on time"

    age_str = f" · position {int(round(age_sec/60))} min old" if age_sec >= 60 else ""

    dest = f" → {headsign}" if headsign else ""
    return f"**Route {route_disp}**{dest} · {eta_str}{delay_str}{age_str}"
