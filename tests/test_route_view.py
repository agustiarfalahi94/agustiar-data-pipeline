import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils import route_view


def _stops(*rows):
    """(stop_id, stop_name, minutes_from_start) -> stop entries."""
    return [{'stop_id': sid, 'stop_name': name, 'stop_lat': 3.0, 'stop_lon': 101.0,
             'arrival_seconds': 21600 + mins * 60}
            for sid, name, mins in rows]


LOOP = _stops(('S1', 'LRT AWAN BESAR', 0),
              ('S2', 'KM1 BUKIT JALIL', 1),
              ('S3', 'GREEN AVENUE CONDOMINIUM', 32),
              ('S1', 'LRT AWAN BESAR', 40))

LINE = _stops(('A', 'FIRST', 0), ('B', 'MIDDLE', 5), ('C', 'LAST', 12))


# ── journey times ──────────────────────────────────────────────────────

def test_offsets_are_measured_from_the_tapped_stop():
    rows = route_view.build_stop_rows(LOOP, 'S1')
    assert [r['offset_minutes'] for r in rows] == [0, 1, 32, 40]


def test_the_stop_named_after_the_destination_is_thirty_two_minutes_away():
    # The failure this feature exists to prevent: boarding at S1 and waiting
    # for the stop named after the building costs 32 minutes, while the useful
    # stop is 1 minute out and named after a shopping mall.
    rows = route_view.build_stop_rows(LOOP, 'S1')
    by_name = {r['stop_name']: r['offset_minutes'] for r in rows}
    assert by_name['KM1 BUKIT JALIL'] == 1
    assert by_name['GREEN AVENUE CONDOMINIUM'] == 32


def test_offsets_are_measured_from_a_mid_route_tapped_stop():
    rows = route_view.build_stop_rows(LINE, 'B')
    assert [r['offset_minutes'] for r in rows] == [-5, 0, 7]


def test_offsets_anchor_on_the_first_occurrence_of_a_repeated_stop():
    # S1 appears at both ends of the loop. Anchoring on the later one would
    # make every other stop negative.
    rows = route_view.build_stop_rows(LOOP, 'S1')
    assert rows[0]['offset_minutes'] == 0
    assert rows[-1]['offset_minutes'] == 40


def test_sequence_numbers_are_one_based_and_in_order():
    assert [r['seq'] for r in route_view.build_stop_rows(LOOP, 'S1')] == [1, 2, 3, 4]


# ── the tapped stop ────────────────────────────────────────────────────

def test_every_occurrence_of_the_tapped_stop_is_marked():
    rows = route_view.build_stop_rows(LOOP, 'S1')
    assert [r['is_tapped'] for r in rows] == [True, False, False, True]


def test_nothing_is_marked_when_the_tapped_stop_is_absent():
    rows = route_view.build_stop_rows(LOOP, 'ZZ')
    assert not any(r['is_tapped'] for r in rows)
    assert all(r['offset_minutes'] is None for r in rows), \
        "without an anchor there is no journey time to state"


# ── near-you marks ─────────────────────────────────────────────────────

def test_near_marks_are_applied_only_to_supplied_stops():
    nearby = {'S2': {'distance_m': 150.0, 'walk_label': '~3 min walk'},
              'S3': {'distance_m': 152.0, 'walk_label': '~3 min walk'}}
    rows = route_view.build_stop_rows(LOOP, 'S1', nearby)
    assert rows[0]['near'] is None
    assert rows[1]['near']['distance_m'] == 150.0
    assert rows[2]['near']['walk_label'] == '~3 min walk'


def test_near_marks_are_absent_when_no_map_is_given():
    assert all(r['near'] is None for r in route_view.build_stop_rows(LOOP, 'S1'))


# ── malformed input ────────────────────────────────────────────────────

def test_an_entry_without_a_time_still_renders_without_an_offset():
    broken = [{'stop_id': 'S1', 'stop_name': 'A', 'arrival_seconds': 0},
              {'stop_id': 'S2', 'stop_name': 'B'}]
    rows = route_view.build_stop_rows(broken, 'S1')
    assert rows[1]['offset_minutes'] is None
    assert rows[1]['stop_name'] == 'B'


def test_an_empty_pattern_yields_no_rows():
    assert route_view.build_stop_rows([], 'S1') == []


# ── labels ─────────────────────────────────────────────────────────────

def test_a_loop_is_labelled_by_where_it_starts():
    # "by its terminus" would read "to LRT AWAN BESAR", which explains nothing
    # when that is also where it began.
    assert route_view.pattern_label(LOOP) == 'loop from LRT AWAN BESAR'


def test_a_line_is_labelled_by_its_last_stop():
    assert route_view.pattern_label(LINE) == 'to LAST'


def test_a_published_headsign_wins():
    assert route_view.pattern_label(LOOP, 'Pavilion Bukit Jalil') == 'Pavilion Bukit Jalil'


def test_a_blank_headsign_falls_back_to_the_shape():
    assert route_view.pattern_label(LOOP, '   ') == 'loop from LRT AWAN BESAR'


def test_pattern_label_survives_an_empty_pattern():
    assert route_view.pattern_label([]) == ''


# ── unique titles ──────────────────────────────────────────────────────

def test_titles_state_the_stop_count_and_running_time():
    titles = route_view.pattern_titles([LOOP], [''])
    assert titles == ['loop from LRT AWAN BESAR · 4 stops · ~40 min']


def test_two_patterns_never_share_a_title():
    # Same shape, same endpoints, same length: only a suffix can separate them.
    other = _stops(('S1', 'LRT AWAN BESAR', 0), ('S9', 'VIA SOMEWHERE', 1),
                   ('S3', 'GREEN AVENUE CONDOMINIUM', 32), ('S1', 'LRT AWAN BESAR', 40))
    titles = route_view.pattern_titles([LOOP, other], ['', ''])
    assert len(set(titles)) == 2, titles
    assert titles[0] != titles[1]


def test_distinct_patterns_keep_their_natural_titles():
    titles = route_view.pattern_titles([LOOP, LINE], ['', ''])
    assert titles[0].startswith('loop from LRT AWAN BESAR')
    assert titles[1].startswith('to LAST')


def test_titles_tolerate_a_short_headsign_list():
    assert len(route_view.pattern_titles([LOOP, LINE], [])) == 2
