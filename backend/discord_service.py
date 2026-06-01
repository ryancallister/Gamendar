import urllib.request
import urllib.error
import json
from datetime import datetime


def get_setting(db, key, default=None):
    row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def log_discord(db, message_type, success, event_id=None, date_str=None, error=None):
    db.execute(
        'INSERT INTO discord_log (event_id, message_type, date, success, error) VALUES (?, ?, ?, ?, ?)',
        (event_id, message_type, date_str, 1 if success else 0, error)
    )
    db.commit()


def send_webhook(webhook_url, payload):
    """POST an embed payload to a Discord webhook URL. Returns (success, error_msg)."""
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        webhook_url,
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


def discord_configured(db):
    webhook_url = get_setting(db, 'discord_webhook_url')
    enabled = get_setting(db, 'discord_enabled', 'false')
    return bool(webhook_url and enabled == 'true')


# ── Message builders ──────────────────────────────────────────────

def build_event_announcement(event):
    start = event['week_start']
    end = event['week_end']
    title = event['title']
    desc = event.get('description') or ''
    return {
        'embeds': [{
            'title': f'📅 New event: {title}',
            'description': desc if desc else 'Mark your availability for the week!',
            'color': 0x6c8ef5,
            'fields': [
                {'name': 'Week', 'value': f'{fmt_date(start)} – {fmt_date(end)}', 'inline': False},
                {'name': 'Action', 'value': 'Log in to Gamendar and mark your availability.', 'inline': False}
            ],
            'footer': {'text': 'Gamendar'}
        }]
    }


def build_daily_summary(event, availability_rows, users, summary_date):
    available  = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'available']
    unavailable = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'unavailable']
    maybe      = [r for r in availability_rows if r['date'] == summary_date and r['status'] == 'maybe']
    responded_ids = {r['user_id'] for r in availability_rows if r['date'] == summary_date}
    no_response = [u for u in users if u['id'] not in responded_ids]

    def names(items, key='username'):
        return ', '.join(r[key] for r in items) or '—'

    total = len(users)
    avail_count = len(available)
    color = 0x3ecf8e if avail_count == total else (0xf5a623 if avail_count > 0 else 0xf06b6b)

    fields = [
        {'name': f'✅ Available ({len(available)})',   'value': names(available),   'inline': True},
        {'name': f'❌ Unavailable ({len(unavailable)})', 'value': names(unavailable), 'inline': True},
        {'name': f'🤔 Maybe ({len(maybe)})',           'value': names(maybe),       'inline': True},
    ]
    if no_response:
        fields.append({'name': f'⏳ No response ({len(no_response)})', 'value': names(no_response), 'inline': False})

    return {
        'embeds': [{
            'title': f'📋 Daily summary — {fmt_date(summary_date)}',
            'description': f'**{event["title"]}** · {avail_count}/{total} available',
            'color': color,
            'fields': fields,
            'footer': {'text': 'Gamendar'}
        }]
    }


def build_all_available(event, users, summary_date):
    names = ', '.join(u['username'] for u in users)
    return {
        'embeds': [{
            'title': '🎉 Everyone is available!',
            'description': f'All **{len(users)}** members are available on **{fmt_date(summary_date)}** for **{event["title"]}**!',
            'color': 0x3ecf8e,
            'fields': [{'name': 'Who', 'value': names, 'inline': False}],
            'footer': {'text': 'Gamendar'}
        }]
    }


# ── Trigger functions ─────────────────────────────────────────────

def notify_event_created(db, event):
    if not discord_configured(db):
        return
    # Dedup: don't send if already sent for this event in the last 60 seconds
    recent = db.execute(
        "SELECT id FROM discord_log WHERE event_id = ? AND message_type = 'event_created' "
        "AND success = 1 AND sent_at > datetime('now', '-60 seconds')",
        (event['id'],)
    ).fetchone()
    if recent:
        return
    url = get_setting(db, 'discord_webhook_url')
    success, error = send_webhook(url, build_event_announcement(event))
    log_discord(db, 'event_created', success, event_id=event['id'], error=error)


def notify_daily_summary(db, event_id, summary_date):
    if not discord_configured(db):
        return False, 'Discord not configured or disabled'
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
    url = get_setting(db, 'discord_webhook_url')
    success, error = send_webhook(url, build_daily_summary(event, availability, users, summary_date))
    log_discord(db, 'daily_summary', success, event_id=event_id, date_str=summary_date, error=error)
    return success, error


def check_and_notify_all_available(db, event_id, changed_date):
    if not discord_configured(db):
        return
    users = db.execute('SELECT * FROM users WHERE is_active = 1').fetchall()
    if not users:
        return
    active_ids = {u['id'] for u in users}
    rows = db.execute(
        'SELECT user_id, status FROM availability WHERE event_id = ? AND date = ?',
        (event_id, changed_date)
    ).fetchall()
    # Only count active users
    avail_ids = {r['user_id'] for r in rows if r['status'] == 'available' and r['user_id'] in active_ids}
    if avail_ids != active_ids:
        return
    already = db.execute(
        'SELECT id FROM discord_log WHERE event_id = ? AND date = ? AND message_type = ? AND success = 1',
        (event_id, changed_date, 'all_available')
    ).fetchone()
    if already:
        return
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    url = get_setting(db, 'discord_webhook_url')
    success, error = send_webhook(url, build_all_available(event, users, changed_date))
    log_discord(db, 'all_available', success, event_id=event_id, date_str=changed_date, error=error)


def notify_event_announcement(db, event_id):
    if not discord_configured(db):
        return False, 'Discord not configured or disabled'
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return False, 'Event not found'
    url = get_setting(db, 'discord_webhook_url')
    success, error = send_webhook(url, build_event_announcement(event))
    log_discord(db, 'event_created', success, event_id=event_id, error=error)
    return success, error


def fmt_date(iso):
    try:
        d = datetime.strptime(iso, '%Y-%m-%d')
        return f"{d.strftime('%a, %b')} {d.day}"
    except Exception:
        return iso
