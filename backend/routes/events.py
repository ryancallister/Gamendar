from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import token_required, admin_required
from discord_service import notify_event_created
from signal_service import notify_event_created as signal_notify_event_created
from datetime import datetime

events_bp = Blueprint('events', __name__)

MAX_TITLE = 100
MAX_DESCRIPTION = 500


def _clean_date(value):
    """Return an ISO YYYY-MM-DD string, or None if the value isn't a valid date."""
    try:
        return datetime.strptime(str(value)[:10], '%Y-%m-%d').date().isoformat()
    except (ValueError, TypeError):
        return None


@events_bp.route('/', methods=['GET'])
@token_required
def get_events(current_user):
    db = get_db()
    events = db.execute('''
        SELECT e.*, COALESCE(u.username, '[deleted user]') as created_by_username
        FROM events e
        LEFT JOIN users u ON e.created_by = u.id
        ORDER BY e.week_start DESC
    ''').fetchall()
    return jsonify([dict(e) for e in events])


@events_bp.route('/<int:event_id>', methods=['GET'])
@token_required
def get_event(current_user, event_id):
    db = get_db()
    event = db.execute('''
        SELECT e.*, COALESCE(u.username, '[deleted user]') as created_by_username
        FROM events e
        LEFT JOIN users u ON e.created_by = u.id
        WHERE e.id = ?
    ''', (event_id,)).fetchone()
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    # Get all availability for this event
    availability = db.execute('''
        SELECT a.*, u.username
        FROM availability a
        JOIN users u ON a.user_id = u.id
        WHERE a.event_id = ?
        ORDER BY a.date, u.username
    ''', (event_id,)).fetchall()

    # Get all active users for completeness
    users = db.execute(
        'SELECT id, username, email FROM users WHERE is_active = 1 ORDER BY username'
    ).fetchall()

    return jsonify({
        'event': dict(event),
        'availability': [dict(a) for a in availability],
        'users': [dict(u) for u in users]
    })


@events_bp.route('/', methods=['POST'])
@admin_required
def create_event(current_user):
    data = request.get_json()
    if not data or not data.get('title') or not data.get('week_start') or not data.get('week_end'):
        return jsonify({'error': 'Title, week_start, and week_end required'}), 400

    title = str(data['title']).strip()[:MAX_TITLE]
    description = str(data.get('description', '')).strip()[:MAX_DESCRIPTION]
    week_start = _clean_date(data['week_start'])
    week_end = _clean_date(data['week_end'])

    if not title:
        return jsonify({'error': 'Title required'}), 400
    if not week_start or not week_end:
        return jsonify({'error': 'week_start and week_end must be YYYY-MM-DD dates'}), 400
    if week_end < week_start:
        return jsonify({'error': 'week_end must not be before week_start'}), 400

    db = get_db()
    cursor = db.execute(
        'INSERT INTO events (title, description, week_start, week_end, created_by) VALUES (?, ?, ?, ?, ?)',
        (title, description, week_start, week_end, current_user['id'])
    )
    db.commit()

    event = db.execute('SELECT * FROM events WHERE id = ?', (cursor.lastrowid,)).fetchone()
    try:
        notify_event_created(db, dict(event))
    except Exception as e:
        print(f'Discord notify error: {e}')
    try:
        signal_notify_event_created(db, dict(event))
    except Exception as e:
        print(f'Signal notify error: {e}')
    return jsonify(dict(event)), 201


@events_bp.route('/<int:event_id>', methods=['PUT'])
@admin_required
def update_event(current_user, event_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    # Apply the same limits as creation rather than writing whatever arrives.
    title = str(data.get('title', event['title'])).strip()[:MAX_TITLE]
    description = str(data.get('description') or '').strip()[:MAX_DESCRIPTION]
    week_start = _clean_date(data.get('week_start', event['week_start']))
    week_end = _clean_date(data.get('week_end', event['week_end']))

    if not title:
        return jsonify({'error': 'Title required'}), 400
    if not week_start or not week_end:
        return jsonify({'error': 'week_start and week_end must be YYYY-MM-DD dates'}), 400
    if week_end < week_start:
        return jsonify({'error': 'week_end must not be before week_start'}), 400

    db.execute(
        'UPDATE events SET title = ?, description = ?, week_start = ?, week_end = ? WHERE id = ?',
        (title, description, week_start, week_end, event_id)
    )
    db.commit()
    return jsonify({'message': 'Event updated'})


@events_bp.route('/<int:event_id>', methods=['DELETE'])
@admin_required
def delete_event(current_user, event_id):
    db = get_db()
    db.execute('DELETE FROM availability WHERE event_id = ?', (event_id,))
    db.execute('DELETE FROM events WHERE id = ?', (event_id,))
    db.commit()
    return jsonify({'message': 'Event deleted'})
