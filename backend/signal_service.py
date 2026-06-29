import urllib.request
import urllib.error
import json
from datetime import datetime


def get_setting(db, key, default=None):
    row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def log_signal(db, message_type, success, event_id=None, date_str=None, error=None):
    db.execute(
        'INSERT INTO signal_log (event_id, message_type, date, success, error) VALUES (?, ?, ?, ?, ?)',
        (event_id, message_type, date_str, 1 if success else 0, error)
    )
    db.commit()


def send_signal_message(api_url, sender, recipient, message):
    """POST to signal-cli-rest-api /v2/send. Returns (success, error_msg)."""
    url = f'{api_url.rstrip("/")}/v2/send'
    payload = {
        'message': message,
        'number': sender,
        'recipients': [recipient]
    }
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            'Content-Type': 'application/json',
            'User-Agent': 'Gamendar/1.0'
        },
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return True, None
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', errors='replace')
        return False, f'HTTP {e.code}: {body[:200]}'
    except Exception as e:
        return False, str(e)


def signal_configured(db):
    api_url = get_setting(db, 'signal_api_url')
    sender = get_setting(db, 'signal_sender')
    recipient = get_setting(db, 'signal_recipient')
    enabled = get_setting(db, 'signal_enabled', 'false')
    return bool(api_url and sender and recipient and enabled == 'true')


# ── Default templates (used if no custom template is set) ────────
DEFAULT_TEMPLATE_EVENT_CREATED = (
    "📅 New event: {title}\n"
    "Week: {week_start} – {week_end}\n"
    "{description}"
    "Log in to Gamendar and mark your availability."
)

DEFAULT_TEMPLATE_DAILY_SUMMARY = (
    "📋 Daily summary — {summary_date}\n"
    "{title} · {available_count}/{total_count} available\n"
    "\n"
    "✅ Available ({available_count}): {available_names}\n"
    "❌ Unavailable ({unavailable_count}): {unavailable_names}\n"
    "🤔 Maybe ({maybe_count}): {maybe_names}\n"
    "{no_response_line}"
)

DEFAULT_TEMPLATE_ALL_AVAILABLE = (
    "🎉 Everyone is available on {summary_date}!\n"
    "{title} · All {total_count} members are free.\n"
    "Who: {available_names}"
)

TEMPLATE_DEFAULTS = {
    'signal_template_event_created': DEFAULT_TEMPLATE_EVENT_CREATED,
    'signal_template_daily_summary': DEFAULT_TEMPLATE_DAILY_SUMMARY,
    'signal_template_all_available': DEFAULT_TEMPLATE_ALL_AVAILABLE,
}

TEMPLATE_PLACEHOLDERS = {
    'signal_template_event_created': ['title', 'description', 'week_start', 'week_end'],
    'signal_template_daily_summary': ['title', 'summary_date', 'available_count', 'unavailable_count',
                                       'maybe_count', 'no_response_count', 'total_count',
                                       'available_names', 'unavailable_names', 'maybe_names',
                                       'no_response_names', 'no_response_line'],
    'signal_template_all_available': ['title', 'summary_date', 'total_count', 'available_names'],
}


def render_template(template, **kwargs):
    # Safely format a template string, leaving unknown placeholders untouched.
    class SafeDict(dict):
        def __missing__(self, key):
            return '{' + key + '}'
    try:
        return template.format_map(SafeDict(**kwargs))
    except Exception:
        return template


# ── Message builders (plain text for Signal) ──────────────────────

def build_event_announcement(event, db=None):
    start = fmt_date(event['week_start'])
    end = fmt_date(event['week_end'])
    desc = event.get('description') or ''

    template = None
    if db is not None:
        template = get_setting(db, 'signal_template_event_created')
    if not template:
        template = DEFAULT_TEMPLATE_EVENT_CREATED

    return render_template(
        template,
        title=event['title'],
        description=(desc + '\n') if desc else '',
        week_start=start,
        week_end=end,
    )


def build_daily_summary(event, availability_rows, users, summary_date, db=None):
    available   = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'available']
    unavailable = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'unavailable']
    maybe       = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'maybe']
    responded_ids = {r['user_id'] for r in availability_rows if r['date'] == summary_date}
    no_response = [u for u in users if u['id'] not in responded_ids]

    def names(items, key='username'):
        return ', '.join(r[key] for r in items) or '—'

    total = len(users)
    avail_count = len(available)

    no_response_line = f"⏳ No response ({len(no_response)}): {names(no_response)}" if no_response else ''

    template = None
    if db is not None:
        template = get_setting(db, 'signal_template_daily_summary')
    if not template:
        template = DEFAULT_TEMPLATE_DAILY_SUMMARY

    return render_template(
        template,
        title=event['title'],
        summary_date=fmt_date(summary_date),
        available_count=avail_count,
        unavailable_count=len(unavailable),
        maybe_count=len(maybe),
        no_response_count=len(no_response),
        total_count=total,
        available_names=names(available),
        unavailable_names=names(unavailable),
        maybe_names=names(maybe),
        no_response_names=names(no_response),
        no_response_line=no_response_line,
    )


def build_all_available(event, users, summary_date, db=None):
    names = ', '.join(u['username'] for u in users)

    template = None
    if db is not None:
        template = get_setting(db, 'signal_template_all_available')
    if not template:
        template = DEFAULT_TEMPLATE_ALL_AVAILABLE

    return render_template(
        template,
        title=event['title'],
        summary_date=fmt_date(summary_date),
        total_count=len(users),
        available_names=names,
    )


# ── Trigger functions ─────────────────────────────────────────────

def notify_event_created(db, event):
    if not signal_configured(db):
        return
    # Dedup: don't send if already sent for this event in the last 60 seconds
    recent = db.execute(
        "SELECT id FROM signal_log WHERE event_id = ? AND message_type = 'event_created' "
        "AND success = 1 AND sent_at > datetime('now', '-60 seconds')",
        (event['id'],)
    ).fetchone()
    if recent:
        return
    try:
        api_url   = get_setting(db, 'signal_api_url')
        sender    = get_setting(db, 'signal_sender')
        recipient = get_setting(db, 'signal_recipient')
        message   = build_event_announcement(event, db=db)
        success, error = send_signal_message(api_url, sender, recipient, message)
        log_signal(db, 'event_created', success, event_id=event['id'], error=error)
    except Exception as e:
        print(f'Signal notify_event_created error: {e}')
        try:
            log_signal(db, 'event_created', False, event_id=event.get('id'), error=str(e))
        except Exception:
            pass


def notify_daily_summary(db, event_id, summary_date):
    if not signal_configured(db):
        return False, 'Signal not configured or disabled'
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return False, 'Event not found'
    users = db.execute('SELECT * FROM users WHERE is_active = 1 ORDER BY username').fetchall()
    active_ids = tuple(u['id'] for u in users)
    if not active_ids:
        return False, 'No active users'
    placeholders = ','.join('?' * len(active_ids))
    availability = db.execute(
        f'SELECT a.*, u.username FROM availability a JOIN users u ON a.user_id = u.id WHERE a.event_id = ? AND a.user_id IN ({placeholders})',
        (event_id, *active_ids)
    ).fetchall()
    api_url   = get_setting(db, 'signal_api_url')
    sender    = get_setting(db, 'signal_sender')
    recipient = get_setting(db, 'signal_recipient')
    message   = build_daily_summary(event, availability, users, summary_date, db=db)
    success, error = send_signal_message(api_url, sender, recipient, message)
    log_signal(db, 'daily_summary', success, event_id=event_id, date_str=summary_date, error=error)
    return success, error


def check_and_notify_all_available(db, event_id, changed_date):
    if not signal_configured(db):
        return
    users = db.execute('SELECT * FROM users WHERE is_active = 1').fetchall()
    if not users:
        return
    active_ids = {u['id'] for u in users}
    rows = db.execute(
        'SELECT user_id, status FROM availability WHERE event_id = ? AND date = ?',
        (event_id, changed_date)
    ).fetchall()
    avail_ids = {r['user_id'] for r in rows if r['status'] == 'available' and r['user_id'] in active_ids}
    if avail_ids != active_ids:
        return
    already = db.execute(
        'SELECT id FROM signal_log WHERE event_id = ? AND date = ? AND message_type = ? AND success = 1',
        (event_id, changed_date, 'all_available')
    ).fetchone()
    if already:
        return
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    api_url   = get_setting(db, 'signal_api_url')
    sender    = get_setting(db, 'signal_sender')
    recipient = get_setting(db, 'signal_recipient')
    message   = build_all_available(event, users, changed_date, db=db)
    success, error = send_signal_message(api_url, sender, recipient, message)
    log_signal(db, 'all_available', success, event_id=event_id, date_str=changed_date, error=error)


def notify_event_announcement(db, event_id):
    if not signal_configured(db):
        return False, 'Signal not configured or disabled'
    try:
        event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
        if not event:
            return False, 'Event not found'
        api_url   = get_setting(db, 'signal_api_url')
        sender    = get_setting(db, 'signal_sender')
        recipient = get_setting(db, 'signal_recipient')
        message   = build_event_announcement(dict(event), db=db)
        success, error = send_signal_message(api_url, sender, recipient, message)
        log_signal(db, 'event_created', success, event_id=event_id, error=error)
        return success, error
    except Exception as e:
        print(f'Signal notify_event_announcement error: {e}')
        return False, str(e)


def fmt_date(iso):
    try:
        d = datetime.strptime(iso, '%Y-%m-%d')
        return f"{d.strftime('%a, %b')} {d.day}"
    except Exception:
        return iso
