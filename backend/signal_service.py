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


# ── Message builders (plain text for Signal) ──────────────────────

def build_event_announcement(event):
    start = fmt_date(event['week_start'])
    end = fmt_date(event['week_end'])
    desc = event.get('description') or ''
    lines = [
        f"📅 New event: {event['title']}",
        f"Week: {start} – {end}",
    ]
    if desc:
        lines.append(desc)
    lines.append("Log in to Gamendar and mark your availability.")
    return '\n'.join(lines)


def build_daily_summary(event, availability_rows, users, summary_date):
    available   = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'available']
    unavailable = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'unavailable']
    maybe       = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'maybe']
    responded_ids = {r['user_id'] for r in availability_rows if r['date'] == summary_date}
    no_response = [u for u in users if u['id'] not in responded_ids]

    def names(items, key='username'):
        return ', '.join(r[key] for r in items) or '—'

    total = len(users)
    avail_count = len(available)

    lines = [
        f"📋 Daily summary — {fmt_date(summary_date)}",
        f"{event['title']} · {avail_count}/{total} available",
        "",
        f"✅ Available ({len(available)}): {names(available)}",
        f"❌ Unavailable ({len(unavailable)}): {names(unavailable)}",
        f"🤔 Maybe ({len(maybe)}): {names(maybe)}",
    ]
    if no_response:
        lines.append(f"⏳ No response ({len(no_response)}): {names(no_response)}")
    return '\n'.join(lines)


def build_all_available(event, users, summary_date):
    names = ', '.join(u['username'] for u in users)
    return (
        f"🎉 Everyone is available on {fmt_date(summary_date)}!\n"
        f"{event['title']} · All {len(users)} members are free.\n"
        f"Who: {names}"
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
        message   = build_event_announcement(event)
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
    message   = build_daily_summary(event, availability, users, summary_date)
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
    message   = build_all_available(event, users, changed_date)
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
        message   = build_event_announcement(dict(event))
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
