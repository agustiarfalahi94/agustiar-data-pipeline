"""
route_panel.py
--------------
Route Viewer details and breadcrumb trail panel rendering for Live Map.
"""

import html
import streamlit as st


def render_route_viewer_header(vehicle_id, route_display=""):
    """
    Render Route Viewer header caption for a selected vehicle.
    """
    v_id = html.escape(str(vehicle_id))
    r_disp = html.escape(str(route_display))
    if r_disp:
        return f"🚌 **Vehicle {v_id}** (Route {r_disp})"
    return f"🚌 **Vehicle {v_id}**"
