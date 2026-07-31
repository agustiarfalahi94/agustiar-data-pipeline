import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import eta


def _stops(*coords):
    return [{'stop_id': str(i), 'stop_name': f'S{i}', 'stop_lat': la, 'stop_lon': lo}
            for i, (la, lo) in enumerate(coords)]


def test_haversine_matches_a_known_distance():
    # KL Sentral -> Awan Besar LRT, ~8.1 km apart
    d = eta.haversine_m(3.134620, 101.686855, 3.062131, 101.670555)
    assert 7500 < d < 8700, d


def test_haversine_is_zero_for_the_same_point():
    assert eta.haversine_m(3.1, 101.7, 3.1, 101.7) == 0


def test_nearest_stop_index_picks_the_closest():
    stops = _stops((3.10, 101.70), (3.20, 101.70), (3.30, 101.70))
    idx, dist = eta.nearest_stop_index(stops, 3.199, 101.70)
    assert idx == 1
    assert dist < 200


def test_nearest_stop_index_respects_start_index():
    """The answer must stay ahead of the bus, even when a closer stop is behind it."""
    stops = _stops((3.10, 101.70), (3.20, 101.70), (3.30, 101.70))
    idx, _ = eta.nearest_stop_index(stops, 3.10, 101.70, start_index=2)
    assert idx == 2


def test_nearest_stop_index_on_empty_or_exhausted_list():
    assert eta.nearest_stop_index([], 3.1, 101.7) == (-1, math.inf)
    stops = _stops((3.10, 101.70))
    assert eta.nearest_stop_index(stops, 3.1, 101.7, start_index=5) == (-1, math.inf)


def test_walking_minutes_rounds_up_and_has_a_floor():
    assert eta.walking_minutes(0) == 1
    assert eta.walking_minutes(80) == 1
    assert eta.walking_minutes(81) == 2
    assert eta.walking_minutes(240) == 3
