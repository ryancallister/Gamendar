from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import admin_required
import threading
import time
from datetime import datetime, date, timedelta

recurring_bp = Blueprint('recurring', __name__)

DAY_NAMES = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']


def get_setting(db, key, default=None):
    row = db.execute('SELECT value FROM settings WHERE key = ?', (key,)).fetchone()
    return row['value'] if row else default


def set_setting(db, key, value):
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, str(value))
    )


def recurring_configured(db):
    return get_setting(db, 'recurring_enabled', 'false') == 'true'


def _next_monday(from_date):
    """Monday of the week following from_date."""
    days_ahead = 7 - from_date.weekday()
    return from_date + timedelta(days=days_ahead)


def create_recurring_event(db, admin_id, week_start=None):
    """Create the next weekly event. Returns (event_row, error)."""
    title_tpl = get_setting(db, 'recurring_title', 'Week of {week_start}')
    description = get_setting(db, 'recurring_description', '')

    if week_start is None:
        week_start = _next_monday(date.today())
    week_end = week_start + timedelta(days=6)

    ws, we = week_start.isoformat(), week_end.isoformat()

    # Don't duplicate
    existing = db.execute('SELECT * FROM events WHERE week_start = ?', (ws,)).fetchone()
    if existing:
        return None, 'An event for that week already exists'

    title = title_tpl.replace('{week_start}', ws).replace('{week_end}', we)

    cursor = db.execute(
        'INSERT INTO events (title, description, week_start, week_end, created_by, is_recurring) '
        'VALUES (?, ?, ?, ?, ?, 1)',
        (title[:100], description[:500], ws, we, admin_id)
    )
    db.commit()
    event = db.execute('SELECT * FROM events WHERE id = ?', (cursor.lastrowid,)).fetchone()
    return event, None


# ── Settings ──────────────────────────────────────────────────────

@recurring_bp.route('/settings', methods=['GET'])
@admin_required
def get_recurring_settings(current_user):
    db = get_db()
    return jsonify({
        'recurring_enabled':     get_setting(db, 'recurring_enabled', 'false'),
        'recurring_day':         get_setting(db, 'recurring_day', '4'),      # 0=Mon … 6=Sun
        'recurring_time':        get_setting(db, 'recurring_time', '09:00'),
        'recurring_title':       get_setting(db, 'recurring_title', 'Week of {week_start}'),
        'recurring_description': get_setting(db, 'recurring_description', ''),
        'recurring_last_run':    get_setting(db, 'recurring_last_run', ''),
        'day_names':             DAY_NAMES,
    })


@recurring_bp.route('/settings', methods=['POST'])
@admin_required
def save_recurring_settings(current_user):
    data = request.get_json() or {}
    db = get_db()
    allowed = ['recurring_enabled', 'recurring_day', 'recurring_time',
               'recurring_title', 'recurring_description']
    for key in allowed:
        if key in data:
            set_setting(db, key, str(data[key])[:500])
    db.commit()
    return jsonify({'message': 'Recurring settings saved'})


@recurring_bp.route('/create-now', methods=['POST'])
@admin_required
def create_now(current_user):
    """Manually trigger creation of the next recurring event."""
    data = request.get_json() or {}
    db = get_db()

    week_start = None
    if data.get('week_start'):
        try:
            week_start = datetime.strptime(data['week_start'], '%Y-%m-%d').date()
        except ValueError:
            return jsonify({'error': 'Invalid week_start date'}), 400

    event, error = create_recurring_event(db, current_user['id'], week_start)
    if error:
        return jsonify({'error': error}), 409

    # Fire notifications the same way a manual creation would
    try:
        from discord_service import notify_event_created as discord_notify
        discord_notify(db, dict(event))
    except Exception as e:
        print(f'Discord notify error: {e}')
    try:
        from signal_service import notify_event_created as signal_notify
        signal_notify(db, dict(event))
    except Exception as e:
        print(f'Signal notify error: {e}')

    return jsonify({'message': 'Recurring event created', 'event': dict(event)}), 201


# ── Scheduler ─────────────────────────────────────────────────────

_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_recurring_scheduler(app):
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def run():
        while True:
            time.sleep(60)
            try:
                with app.app_context():
                    db = get_db()
                    if not recurring_configured(db):
                        continue

                    target_day  = int(get_setting(db, 'recurring_day', '4'))
                    target_time = get_setting(db, 'recurring_time', '09:00')

                    now = datetime.now()
                    if now.weekday() != target_day:
                        continue
                    if now.strftime('%H:%M') != target_time:
                        continue

                    # Only once per day
                    today_iso = now.date().isoformat()
                    if get_setting(db, 'recurring_last_run') == today_iso:
                        continue

                    admin = db.execute(
                        "SELECT id FROM users WHERE role = 'admin' AND is_active = 1 LIMIT 1"
                    ).fetchone()
                    if not admin:
                        continue

                    event, error = create_recurring_event(db, admin['id'])
                    set_setting(db, 'recurring_last_run', today_iso)
                    db.commit()

                    if event:
                        print(f'Recurring event created: {event["title"]}')
                        try:
                            from discord_service import notify_event_created as dn
                            dn(db, dict(event))
                        except Exception as e:
                            print(f'Discord notify error: {e}')
                        try:
                            from signal_service import notify_event_created as sn
                            sn(db, dict(event))
                        except Exception as e:
                            print(f'Signal notify error: {e}')
            except Exception as e:
                print(f'Recurring scheduler error: {e}')

    threading.Thread(target=run, daemon=True).start()
