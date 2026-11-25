"""Recurring blocked-out time, and what it leaves free.

A block is a label, a set of weekdays and a time range: "Work, weekdays,
07:00-17:00". An end before the start means the block runs past midnight, so a
21:00-07:00 bedtime blocks the tail of one day and the head of the next.

Everything here is pure arithmetic on minutes-from-midnight so the awkward part
— working out what is left over once the blocks are taken out of a day — can be
tested without a database.
"""

DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']  # 0 = Monday, as date.weekday()
WEEKDAYS = [0, 1, 2, 3, 4]
WEEKENDS = [5, 6]
MINUTES_PER_DAY = 1440
MAX_LABEL = 60


class BadBlock(Exception):
    pass


# ── Parsing ───────────────────────────────────────────────────────

def parse_hhmm(value):
    """'HH:MM' to minutes from midnight."""
    raw = str(value or '').strip()
    parts = raw.split(':')
    if len(parts) != 2:
        raise BadBlock(f'Expected a time like 09:30, got {raw[:10]!r}')
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        raise BadBlock(f'Expected a time like 09:30, got {raw[:10]!r}')
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        raise BadBlock(f'{raw[:10]!r} is not a real time of day')
    return hours * 60 + minutes


def format_hhmm(minutes):
    minutes %= MINUTES_PER_DAY
    return f'{minutes // 60:02d}:{minutes % 60:02d}'


def parse_days(value):
    """'0,1,2' or [0,1,2] to a sorted list of weekday numbers."""
    if isinstance(value, str):
        items = [p for p in value.split(',') if p.strip() != '']
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        raise BadBlock('Pick at least one day')
    days = set()
    for item in items:
        try:
            day = int(item)
        except (TypeError, ValueError):
            raise BadBlock('Days must be numbers from 0 (Monday) to 6 (Sunday)')
        if not 0 <= day <= 6:
            raise BadBlock('Days must be numbers from 0 (Monday) to 6 (Sunday)')
        days.add(day)
    if not days:
        raise BadBlock('Pick at least one day')
    return sorted(days)


def format_days(days):
    """The short summary shown under a block's label."""
    days = sorted(set(days))
    if days == list(range(7)):
        return 'Every day'
    if days == WEEKDAYS:
        return 'Weekdays'
    if days == WEEKENDS:
        return 'Weekends'
    return ', '.join(DAY_NAMES[d] for d in days)


def describe(block):
    """e.g. 'Weekdays · 21:00–07:00 (next day)'."""
    wraps = block['end'] < block['start']
    span = f"{format_hhmm(block['start'])}–{format_hhmm(block['end'])}"
    return f"{format_days(block['days'])} · {span}{' (next day)' if wraps else ''}"


def clean(label, days, start_time, end_time):
    """Validate one block's fields. Raises BadBlock with something readable."""
    label = str(label or '').strip()[:MAX_LABEL]
    if not label:
        raise BadBlock('Give it a label, like Work or Gym')
    days = parse_days(days)
    start = parse_hhmm(start_time)
    end = parse_hhmm(end_time)
    if start == end:
        raise BadBlock('The start and end times need to differ')
    return {'label': label, 'days': days, 'start': start, 'end': end}


# ── What a day has left ───────────────────────────────────────────

def merge_spans(spans):
    """Sorted, non-overlapping spans. Touching spans join up."""
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def busy_on(blocks, weekday):
    """Minutes blocked out on `weekday`, counting what ran over from yesterday."""
    yesterday = (weekday - 1) % 7
    spans = []
    for block in blocks:
        start, end = block['start'], block['end']
        wraps = end < start
        if weekday in block['days']:
            spans.append((start, MINUTES_PER_DAY) if wraps else (start, end))
        if wraps and yesterday in block['days']:
            spans.append((0, end))
    return merge_spans(spans)


def free_on(blocks, weekday):
    """The gaps between the blocks, in order."""
    gaps = []
    cursor = 0
    for start, end in busy_on(blocks, weekday):
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < MINUTES_PER_DAY:
        gaps.append((cursor, MINUTES_PER_DAY))
    return gaps


def preferred_free_window(blocks, weekday):
    """The stretch to offer as availability, or None when the day is all blocked.

    A day holds one range, so several gaps have to become one. The latest gap
    wins rather than the longest: what people mean by a schedule is "I am free
    once my commitments are done". Picking the longest gap answers "00:00-07:00"
    for a weekday whose night is not explicitly blocked, and "the morning" for
    someone whose only block is lunch — both technically free and neither any
    use for arranging an evening.
    """
    gaps = free_on(blocks, weekday)
    if not gaps:
        return None
    return gaps[-1]


def availability_for(blocks, weekday):
    """What applying the schedule should record for one day.

    Returns (status, start_time, end_time); the times are '' when the whole day
    is free, since "free all day" is better said with no times at all.
    """
    window = preferred_free_window(blocks, weekday)
    if window is None:
        return 'unavailable', '', ''
    if window == (0, MINUTES_PER_DAY):
        return 'available', '', ''
    return 'available', format_hhmm(window[0]), format_hhmm(window[1])
