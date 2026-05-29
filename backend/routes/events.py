from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import token_required, admin_required
from discord_service import notify_event_created
from signal_service import notify_event_created as signal_notify_event_created

events_bp = Blueprint('events', __name__)


@events_bp.route('/', methods=['GET'])
@token_required
def get_events(current_user):
    db = get_db()
    events = db.execute('''
        SELECT e.*, u.username as created_by_username
        FROM events e
        JOIN users u ON e.created_by = u.id
        ORDER BY e.week_start DESC
    ''').fetchall()
    return jsonify([dict(e) for e in events])


@events_bp.route('/<int:event_id>', methods=['GET'])
@token_required
def get_event(current_user, event_id):
    db = get_db()
    event = db.execute('''
        SELECT e.*, u.username as created_by_username
        FROM events e
        JOIN users u ON e.created_by = u.id
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

    db = get_db()
    cursor = db.execute(
        'INSERT INTO events (title, description, week_start, week_end, created_by) VALUES (?, ?, ?, ?, ?)',
        (data['title'], data.get('description', ''), data['week_start'], data['week_end'], current_user['id'])
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
    data = request.get_json()
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    db.execute(
        'UPDATE events SET title = ?, description = ?, week_start = ?, week_end = ? WHERE id = ?',
        (
            data.get('title', event['title']),
            data.get('description', event['description']),
            data.get('week_start', event['week_start']),
            data.get('week_end', event['week_end']),
            event_id
        )
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
