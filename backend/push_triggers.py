"""When a push notification goes out.

Deliberately separate from push_service (the transport) and signal_service
(the Signal channel): these are the moments worth telling someone about, and
each channel decides for itself whether it can deliver them. Push works with
no Signal configured at all.
"""
import push_service
import signal_service as sig


def _log(db, message_type, event_id=None, date_str=None):
    db.execute('INSERT INTO push_log (event_id, message_type, date) VALUES (?, ?, ?)',
               (event_id, message_type, date_str))
    db.commit()


def _already(db, message_type, event_id, date_str):
    return db.execute(
        'SELECT 1 FROM push_log WHERE event_id = ? AND message_type = ? AND date IS ? LIMIT 1',
        (event_id, message_type, date_str)
    ).fetchone() is not None


def _idle(db):
    """Nothing to do when push is unusable or nobody has turned it on."""
    return not push_service.available() or not push_service.anyone_subscribed(db)


def event_created(db, event):
    if _idle(db) or _already(db, 'event_created', event['id'], None):
        return
    desc = (event['description'] if 'description' in event.keys() else None) or ''
    push_service.notify_all_from_template(
        db, 'signal_template_event_created',
        title=event['title'],
        description=(desc + '\n') if desc else '',
        week_start=sig.fmt_date(event['week_start']),
        week_end=sig.fmt_date(event['week_end']),
    )
    _log(db, 'event_created', event['id'])


def all_available(db, event_id, date_str):
    """Fires once per event+day, however many saves it takes to get there."""
    if _idle(db) or _already(db, 'all_available', event_id, date_str):
        return
    users = sig.everyone_available(db, event_id, date_str)
    if users is None:
        return
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return
    push_service.notify_all_from_template(
        db, 'signal_template_all_available',
        title=event['title'],
        summary_date=sig.fmt_date(date_str),
        total_count=len(users),
        available_names=', '.join(u['username'] for u in users),
    )
    _log(db, 'all_available', event_id, date_str)


def daily_summary(db, event_id, date_str):
    if _idle(db) or _already(db, 'daily_summary', event_id, date_str):
        return
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return
    users = db.execute('SELECT * FROM users WHERE is_active = 1 ORDER BY username').fetchall()
    if not users:
        return
    placeholders = ','.join('?' * len(users))
    availability = db.execute(
        f'SELECT a.*, u.username FROM availability a JOIN users u ON a.user_id = u.id '
        f'WHERE a.event_id = ? AND a.user_id IN ({placeholders})',
        (event_id, *[u['id'] for u in users])
    ).fetchall()
    # Reuse the Signal builder so both channels say exactly the same thing.
    title, body = push_service.split_message(
        sig.build_daily_summary(event, availability, users, date_str, db=db))
    push_service.notify_all(db, title, body)
    _log(db, 'daily_summary', event_id, date_str)


def late_changes(db, event, date_str, changes):
    """Called from the change flush, which has already done the batching."""
    if _idle(db):
        return
    title, body = push_service.split_message(
        sig.build_changes_message(db, event, date_str, changes))
    push_service.notify_all(db, title, body)
    _log(db, 'changes', event['id'], date_str)
