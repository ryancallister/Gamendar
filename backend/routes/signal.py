from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import admin_required
from signal_service import (
    get_setting, send_signal_message, signal_configured,
    notify_daily_summary, notify_event_announcement,
    TEMPLATE_DEFAULTS, TEMPLATE_PLACEHOLDERS, render_template,
    build_event_announcement, build_daily_summary, build_all_available
)
import threading
import time
from datetime import datetime, date

signal_bp = Blueprint('signal', __name__)


# ── Settings ──────────────────────────────────────────────────────

@signal_bp.route('/settings', methods=['GET'])
@admin_required
def get_signal_settings(current_user):
    db = get_db()
    return jsonify({
        'signal_api_url':   get_setting(db, 'signal_api_url', ''),
        'signal_sender':    get_setting(db, 'signal_sender', ''),
        'signal_recipient': get_setting(db, 'signal_recipient', ''),
        'signal_enabled':   get_setting(db, 'signal_enabled', 'false'),
        'signal_daily_time': get_setting(db, 'signal_daily_time', '09:00'),
        'signal_configured': signal_configured(db),
    })


@signal_bp.route('/settings', methods=['POST'])
@admin_required
def save_signal_settings(current_user):
    data = request.get_json()
    db = get_db()
    allowed = ['signal_api_url', 'signal_sender', 'signal_recipient',
               'signal_enabled', 'signal_daily_time']
    for key in allowed:
        if key in data:
            db.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (key, str(data[key]))
            )
    db.commit()
    return jsonify({'message': 'Settings saved'})


@signal_bp.route('/test', methods=['POST'])
@admin_required
def test_signal(current_user):
    db = get_db()
    api_url   = get_setting(db, 'signal_api_url')
    sender    = get_setting(db, 'signal_sender')
    recipient = get_setting(db, 'signal_recipient')
    if not api_url or not sender or not recipient:
        return jsonify({'error': 'API URL, sender number, and recipient are all required'}), 400
    success, error = send_signal_message(
        api_url, sender, recipient,
        '✅ Gamendar Signal integration is working correctly!'
    )
    if success:
        return jsonify({'message': 'Test message sent!'})
    return jsonify({'error': f'Failed: {error}'}), 400


# ── Message templates ─────────────────────────────────────────────

TEMPLATE_KEYS = ['signal_template_event_created', 'signal_template_daily_summary',
                  'signal_template_all_available']


@signal_bp.route('/templates', methods=['GET'])
@admin_required
def get_templates(current_user):
    db = get_db()
    result = {}
    for key in TEMPLATE_KEYS:
        custom = get_setting(db, key)
        result[key] = {
            'value': custom or TEMPLATE_DEFAULTS[key],
            'is_custom': bool(custom),
            'default': TEMPLATE_DEFAULTS[key],
            'placeholders': TEMPLATE_PLACEHOLDERS[key],
        }
    return jsonify(result)


@signal_bp.route('/templates', methods=['POST'])
@admin_required
def save_templates(current_user):
    data = request.get_json() or {}
    db = get_db()
    for key in TEMPLATE_KEYS:
        if key not in data:
            continue
        val = str(data[key]).strip()
        if not val:
            # Empty means revert to default — delete the override
            db.execute('DELETE FROM settings WHERE key = ?', (key,))
        else:
            db.execute(
                'INSERT INTO settings (key, value) VALUES (?, ?) '
                'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                (key, val)
            )
    db.commit()
    return jsonify({'message': 'Templates saved'})


@signal_bp.route('/templates/<string:key>/reset', methods=['POST'])
@admin_required
def reset_template(current_user, key):
    if key not in TEMPLATE_KEYS:
        return jsonify({'error': 'Unknown template'}), 400
    db = get_db()
    db.execute('DELETE FROM settings WHERE key = ?', (key,))
    db.commit()
    return jsonify({'message': 'Reset to default', 'value': TEMPLATE_DEFAULTS[key]})


@signal_bp.route('/templates/preview', methods=['POST'])
@admin_required
def preview_template(current_user):
    """Render a template with realistic sample data so the admin can see the output."""
    data = request.get_json() or {}
    key = data.get('key')
    template = data.get('template', '')
    if key not in TEMPLATE_KEYS:
        return jsonify({'error': 'Unknown template'}), 400

    sample_event = {'title': 'Game Night Week', 'description': 'Weekly co-op session',
                     'week_start': '2026-06-22', 'week_end': '2026-06-28'}

    if key == 'signal_template_event_created':
        rendered = render_template(
            template, title=sample_event['title'],
            description=sample_event['description'] + '\n',
            week_start='Mon, Jun 22', week_end='Sun, Jun 28'
        )
    elif key == 'signal_template_daily_summary':
        rendered = render_template(
            template, title=sample_event['title'], summary_date='Thu, Jun 25',
            available_count=3, unavailable_count=1, maybe_count=1, no_response_count=0,
            total_count=5, available_names='admin, plat, chubb',
            unavailable_names='Volve', maybe_names='Wank',
            no_response_names='—', no_response_line=''
        )
    else:  # all_available
        rendered = render_template(
            template, title=sample_event['title'], summary_date='Thu, Jun 25',
            total_count=5, available_names='admin, plat, chubb, Volve, Wank'
        )

    return jsonify({'preview': rendered})


# ── Manual triggers ───────────────────────────────────────────────

@signal_bp.route('/send/announcement/<int:event_id>', methods=['POST'])
@admin_required
def send_announcement(current_user, event_id):
    db = get_db()
    success, error = notify_event_announcement(db, event_id)
    if success:
        return jsonify({'message': 'Announcement sent via Signal!'})
    return jsonify({'error': error or 'Failed to send'}), 400


@signal_bp.route('/send/summary/<int:event_id>', methods=['POST'])
@admin_required
def send_summary(current_user, event_id):
    data = request.get_json() or {}
    summary_date = data.get('date', date.today().isoformat())
    db = get_db()
    success, error = notify_daily_summary(db, event_id, summary_date)
    if success:
        return jsonify({'message': f'Summary for {summary_date} sent!'})
    return jsonify({'error': error or 'Failed to send'}), 400


@signal_bp.route('/log', methods=['GET'])
@admin_required
def get_signal_log(current_user):
    db = get_db()
    rows = db.execute('''
        SELECT sl.*, e.title as event_title
        FROM signal_log sl
        LEFT JOIN events e ON sl.event_id = e.id
        ORDER BY sl.sent_at DESC
        LIMIT 50
    ''').fetchall()
    return jsonify([dict(r) for r in rows])


# ── Daily summary scheduler ───────────────────────────────────────

_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_signal_scheduler(app):
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def run():
        last_run_date = None
        while True:
            time.sleep(60)
            try:
                with app.app_context():
                    db = get_db()
                    if not signal_configured(db):
                        continue
                    daily_time = get_setting(db, 'signal_daily_time', '09:00')
                    now = datetime.now()
                    today_str = now.strftime('%H:%M')
                    today_date = now.date().isoformat()
                    if today_str == daily_time and last_run_date != today_date:
                        last_run_date = today_date
                        events = db.execute(
                            'SELECT * FROM events WHERE week_start <= ? AND week_end >= ?',
                            (today_date, today_date)
                        ).fetchall()
                        for event in events:
                            # Dedup: skip if already sent in the last 10 minutes
                            recent = db.execute(
                                "SELECT id FROM signal_log WHERE event_id = ? AND date = ? "
                                "AND message_type = 'daily_summary' AND success = 1 "
                                "AND sent_at > datetime('now', '-10 minutes')",
                                (event['id'], today_date)
                            ).fetchone()
                            if not recent:
                                notify_daily_summary(db, event['id'], today_date)
            except Exception as e:
                print(f'Signal scheduler error: {e}')

    threading.Thread(target=run, daemon=True).start()
