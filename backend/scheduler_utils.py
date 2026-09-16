"""Shared timing helpers for the background schedulers.

The schedulers wake roughly once a minute. Comparing the clock to a target
time with == means a single missed tick (loop drift, a slow iteration, a
container restart straddling the minute) silently skips the whole day's run.
These helpers use a "has the time passed, and have we run yet today?" model
instead, with the last-run marker kept in the settings table so it also
survives a restart.
"""

DEFAULT_TIME = '09:00'


def parse_hhmm(value, default=DEFAULT_TIME):
    """Parse 'HH:MM' into an (hour, minute) tuple, falling back to default."""
    for candidate in (value, default):
        try:
            hour, minute = str(candidate).strip().split(':')
            hour, minute = int(hour), int(minute)
        except (ValueError, AttributeError):
            continue
        if 0 <= hour < 24 and 0 <= minute < 60:
            return hour, minute
    return 9, 0


def is_due(now, target_time, default=DEFAULT_TIME):
    """True once the clock has reached target_time on the current day."""
    return (now.hour, now.minute) >= parse_hhmm(target_time, default)


def claim_daily_run(db, key, today_iso):
    """Mark `key` as having run today. Returns False if it already ran.

    Claiming before the work is done means a failed send isn't retried in a
    tight loop; the per-message dedup checks in the services are the backstop.
    """
    row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    if row and row['value'] == today_iso:
        return False
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, today_iso)
    )
    db.commit()
    return True
