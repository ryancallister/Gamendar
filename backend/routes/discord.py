from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import admin_required
from discord_service import (
    get_setting, send_webhook, discord_configured,
    notify_daily_summary, notify_event_announcement, log_discord
)
import threading
import time
from datetime import datetime, date

discord_bp = Blueprint('discord', __name__)


# ── Settings ──────────────────────────────────────────────────────

@discord_bp.route('/settings', methods=['GET'])
@admin_required
def get_discord_settings(current_user):
    db = get_db()
    webhook_url = get_setting(db, 'discord_webhook_url', '')
    # Mask the token portion of the URL for display
    masked = _mask_webhook(webhook_url)
    return jsonify({
        'discord_webhook_url': masked,
        'discord_webhook_set': bool(webhook_url),
        'discord_enabled': get_setting(db, 'discord_enabled', 'false'),
        'discord_daily_time': get_setting(db, 'discord_daily_time', '09:00'),
        'discord_configured': discord_configured(db),
    })


@discord_bp.route('/settings', methods=['POST'])
@admin_required
def save_discord_settings(current_user):
    data = request.get_json()
    db = get_db()
    allowed = ['discord_webhook_url', 'discord_enabled', 'discord_daily_time']
    for key in allowed:
        if key not in data:
            continue
        val = str(data[key])
        # Don't overwrite webhook URL if it's the masked placeholder
        if key == 'discord_webhook_url' and '•' in val:
            continue
        db.execute(
            'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, val)
        )
    db.commit()
    return jsonify({'message': 'Settings saved'})


@discord_bp.route('/test', methods=['POST'])
@admin_required
def test_discord(current_user):
    db = get_db()
    webhook_url = get_setting(db, 'discord_webhook_url')
    if not webhook_url:
        return jsonify({'error': 'Webhook URL is not set'}), 400
    payload = {
        'embeds': [{
            'title': '✅ Gamendar connected!',
            'description': 'Discord webhook integration is working correctly.',
            'color': 0x6c8ef5,
            'footer': {'text': 'Gamendar'}
        }]
    }
    success, error = send_webhook(webhook_url, payload)
    if success:
        return jsonify({'message': 'Test message sent!'})
    return jsonify({'error': f'Failed: {error}'}), 400


# ── Manual triggers ───────────────────────────────────────────────

@discord_bp.route('/send/announcement/<int:event_id>', methods=['POST'])
@admin_required
def send_announcement(current_user, event_id):
    db = get_db()
    success, error = notify_event_announcement(db, event_id)
    if success:
        return jsonify({'message': 'Announcement sent to Discord!'})
    return jsonify({'error': error or 'Failed to send'}), 400


@discord_bp.route('/send/summary/<int:event_id>', methods=['POST'])
@admin_required
def send_summary(current_user, event_id):
    data = request.get_json() or {}
    summary_date = data.get('date', date.today().isoformat())
    db = get_db()
    success, error = notify_daily_summary(db, event_id, summary_date)
    if success:
        return jsonify({'message': f'Summary for {summary_date} sent!'})
    return jsonify({'error': error or 'Failed to send'}), 400


@discord_bp.route('/log', methods=['GET'])
@admin_required
def get_discord_log(current_user):
    db = get_db()
    rows = db.execute('''
        SELECT dl.*, e.title as event_title
        FROM discord_log dl
        LEFT JOIN events e ON dl.event_id = e.id
        ORDER BY dl.sent_at DESC
        LIMIT 50
    ''').fetchall()
    return jsonify([dict(r) for r in rows])


# ── Helpers ───────────────────────────────────────────────────────

def _mask_webhook(url):
    """Show the base URL but mask the token at the end."""
    if not url:
        return ''
    # Discord webhook format: https://discord.com/api/webhooks/{id}/{token}
    parts = url.rsplit('/', 1)
    if len(parts) == 2 and len(parts[1]) > 8:
        return parts[0] + '/' + parts[1][:6] + '•' * (len(parts[1]) - 6)
    return url


# ── Daily summary scheduler ───────────────────────────────────────

_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler(app):
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
                    if not discord_configured(db):
                        continue
                    daily_time = get_setting(db, 'discord_daily_time', '09:00')
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
                                "SELECT id FROM discord_log WHERE event_id = ? AND date = ? "
                                "AND message_type = 'daily_summary' AND success = 1 "
                                "AND sent_at > datetime('now', '-10 minutes')",
                                (event['id'], today_date)
                            ).fetchone()
                            if not recent:
                                notify_daily_summary(db, event['id'], today_date)
            except Exception as e:
                print(f'Discord scheduler error: {e}')

    threading.Thread(target=run, daemon=True).start()
