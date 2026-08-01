"""
Turning one route's stop sequence into rows a panel can render.

Pure: no Streamlit, no GTFS, no I/O. Everything here is a function over plain
dicts, so loops, repeated stops and multi-pattern routes are all testable
without a browser or a feed.

The case that motivated this module: T580 leaves Stesen LRT Awan Besar,
reaches KM1 Bukit Jalil after one minute, and comes back past Green Avenue
Condominium 32 minutes later on the return leg of a 40-minute loop. The two
stops are 60 m apart on the ground. A rider heading for the condominium who
trusts the stop names rides 32 minutes instead of 1, and the next bus is ~50
minutes away. 100 of Rapid KL's 136 timetabled routes are loops, so this is
the normal shape of the network rather than an oddity.
"""


def build_stop_rows(stops, tapped_stop_id, nearby_by_id=None):
    """
    Display rows for one stop sequence, timed from *tapped_stop_id*.

    offset_minutes is measured from the FIRST occurrence of the tapped stop.
    On a loop the tapped stop appears at both ends; anchoring on the later one
    would make every other stop negative. It is None when the sequence never
    calls at that stop, or when an entry carries no time — a missing offset is
    stated as nothing rather than as a wrong number.

    is_tapped is set on every occurrence, so both ends of a loop are marked and
    the rider can see the route returns.

    nearby_by_id is {stop_id: {'distance_m', 'walk_label'}}, supplied by the
    caller. This module never measures a distance or calls a routing API; it
    only annotates what it is given.
    """
    nearby_by_id = nearby_by_id or {}

    anchor = None
    for entry in stops:
        if entry.get('stop_id') == tapped_stop_id:
            anchor = entry.get('arrival_seconds')
            break

    rows = []
    for index, entry in enumerate(stops, start=1):
        seconds = entry.get('arrival_seconds')
        if anchor is None or seconds is None:
            offset = None
        else:
            offset = round((seconds - anchor) / 60)
        rows.append({
            'seq': index,
            'stop_id': entry.get('stop_id', ''),
            'stop_name': entry.get('stop_name', ''),
            'offset_minutes': offset,
            'is_tapped': entry.get('stop_id') == tapped_stop_id,
            'near': nearby_by_id.get(entry.get('stop_id')),
        })
    return rows


def pattern_label(stops, headsign=''):
    """
    How to name one stop sequence in a heading.

    A published headsign wins: it is the operator's own wording, and it is what
    a rider reads on the front of the bus. These feeds frequently leave it
    blank, hence the fallbacks.

    Labelling by terminus fails on exactly the routes this feature exists for.
    A loop's last stop is its first, so "to LRT Awan Besar" would describe a
    40-minute circle as though it were a destination. A loop is therefore named
    by where it starts.
    """
    if headsign and headsign.strip():
        return headsign.strip()
    if not stops:
        return ''
    first, last = stops[0], stops[-1]
    if first.get('stop_id') == last.get('stop_id'):
        return f"loop from {first.get('stop_name', '')}"
    return f"to {last.get('stop_name', '')}"


def _running_minutes(stops):
    """End-to-end time for a sequence, or None when it cannot be measured."""
    if len(stops) < 2:
        return None
    first = stops[0].get('arrival_seconds')
    last = stops[-1].get('arrival_seconds')
    if first is None or last is None:
        return None
    return round((last - first) / 60)


def pattern_titles(patterns, headsigns):
    """
    One title per pattern, in the same order, guaranteed unique.

    Two patterns of the same route rendering as two identical headings would
    leave a rider unable to tell which one they had opened. Uniqueness is
    therefore constructed here rather than hoped for in the data: the stop
    count and running time separate most collisions, and a numbered suffix
    settles the rest.

    *patterns* are the dicts get_route_patterns returns, or bare stop lists.
    """
    titles = []
    for index, pattern in enumerate(patterns):
        stops = pattern.get('stops', pattern) if isinstance(pattern, dict) else pattern
        headsign = headsigns[index] if index < len(headsigns) else ''
        title = pattern_label(stops, headsign)
        if stops:
            title += f" · {len(stops)} stops"
        minutes = _running_minutes(stops)
        if minutes is not None:
            title += f" · ~{minutes} min"
        titles.append(title)

    seen = {}
    unique = []
    for title in titles:
        seen[title] = seen.get(title, 0) + 1
        unique.append(title if seen[title] == 1 else f"{title} ({seen[title]})")
    return unique
