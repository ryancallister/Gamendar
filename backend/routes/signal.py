from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import admin_required
from signal_service import (
    get_setting, send_signal_message, signal_configured,
    notify_daily_summary, notify_event_announcement
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
