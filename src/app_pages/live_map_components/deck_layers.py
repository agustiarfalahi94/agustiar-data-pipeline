"""
deck_layers.py
--------------
PyDeck map layer construction for Live Map visualization.
"""

import pydeck as pdk


def build_vehicle_layer(display_df, selected_vehicle_id=None):
    """
    Build PyDeck PolygonLayer / IconLayer for vehicle position arrows.
    Differentiates selected vehicle with highlight styling if provided.
    """
    if display_df is None or display_df.empty:
        return None

    return pdk.Layer(
        "PolygonLayer",
        data=display_df,
        get_polygon="arrow_path",
        get_fill_color="[0, 122, 255, 200]",
        get_line_color="[255, 255, 255, 255]",
        get_line_width=1,
        pickable=True,
        auto_highlight=True,
        extruded=False,
    )


def build_stop_rings_layer(stop_candidates, selected_stop_id=None):
    """
    Build PyDeck ScatterplotLayer for nearby stop rings.
    Highlights selected stop ring in magenta.
    """
    if not stop_candidates:
        return None

    rings_data = []
    for s in stop_candidates:
        is_selected = (selected_stop_id and s['stop_id'] == selected_stop_id)
        rings_data.append({
            'stop_id': s['stop_id'],
            'stop_name': s['stop_name'],
            'latitude': s['stop_lat'],
            'longitude': s['stop_lon'],
            'fill_color': [255, 0, 128, 60] if is_selected else [241, 196, 15, 30],
            'line_color': [255, 0, 128, 255] if is_selected else [241, 196, 15, 200],
            'radius': 45 if is_selected else 30,
        })

    return pdk.Layer(
        "ScatterplotLayer",
        data=rings_data,
        get_position="[longitude, latitude]",
        get_fill_color="fill_color",
        get_line_color="line_color",
        get_radius="radius",
        stroked=True,
        filled=True,
        line_width_min_pixels=3,
        pickable=True,
    )


def build_location_marker_layer(user_lat, user_lon):
    """
    Build PyDeck ScatterplotLayer for GPS user location marker (red disc).
    """
    if user_lat is None or user_lon is None:
        return None

    return pdk.Layer(
        "ScatterplotLayer",
        data=[{'latitude': user_lat, 'longitude': user_lon}],
        get_position="[longitude, latitude]",
        get_fill_color="[231, 76, 60, 220]",
        get_line_color="[255, 255, 255, 255]",
        get_radius=50,
        stroked=True,
        filled=True,
        line_width_min_pixels=2,
        pickable=False,
    )


def build_route_shape_layer(shape_points):
    """
    Build PyDeck PathLayer for GTFS route shape or vehicle trail polyline.
    """
    if not shape_points or len(shape_points) < 2:
        return None

    return pdk.Layer(
        "PathLayer",
        data=[{'path': shape_points}],
        get_path="path",
        get_color="[46, 204, 113, 220]",
        width_min_pixels=4,
        pickable=False,
    )
