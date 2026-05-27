from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import token_required, admin_required

availability_bp = Blueprint('availability', __name__)


@availability_bp.route('/event/<int:event_id>', methods=['GET'])
@token_required
def get_event_availability(current_user, event_id):
    db = get_db()
    rows = db.execute('''
        SELECT a.*, u.username
        FROM availability a
        JOIN users u ON a.user_id = u.id
        WHERE a.event_id = ?
        ORDER BY a.date, u.username
    ''', (event_id,)).fetchall()
    return jsonify([dict(r) for r in rows])


@availability_bp.route('/event/<int:event_id>/set', methods=['POST'])
@token_required
def set_availability(current_user, event_id):
    data = request.get_json()
    if not data or not data.get('date') or not data.get('status'):
        return jsonify({'error': 'date and status required'}), 400

    status = data['status']
    if status not in ('available', 'unavailable', 'maybe'):
        return jsonify({'error': 'status must be available, unavailable, or maybe'}), 400

    # Verify event exists
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    db.execute('''
        INSERT INTO availability (user_id, event_id, date, status, note, updated_at)
        VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, event_id, date) DO UPDATE SET
            status = excluded.status,
            note = excluded.note,
            updated_at = CURRENT_TIMESTAMP
    ''', (current_user['id'], event_id, data['date'], status, data.get('note', '')))
    db.commit()

    return jsonify({'message': 'Availability saved'})


@availability_bp.route('/event/<int:event_id>/bulk', methods=['POST'])
@token_required
def set_bulk_availability(current_user, event_id):
    """Set availability for multiple dates at once."""
    data = request.get_json()
    if not data or not isinstance(data.get('entries'), list):
        return jsonify({'error': 'entries array required'}), 400

    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id = ?', (event_id,)).fetchone()
    if not event:
        return jsonify({'error': 'Event not found'}), 404

    for entry in data['entries']:
        if not entry.get('date') or not entry.get('status'):
            continue
        if entry['status'] not in ('available', 'unavailable', 'maybe'):
            continue
        db.execute('''
            INSERT INTO availability (user_id, event_id, date, status, note, updated_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(user_id, event_id, date) DO UPDATE SET
                status = excluded.status,
                note = excluded.note,
                updated_at = CURRENT_TIMESTAMP
        ''', (current_user['id'], event_id, entry['date'], entry['status'], entry.get('note', '')))

    db.commit()
    return jsonify({'message': f'{len(data["entries"])} entries saved'})


@availability_bp.route('/my', methods=['GET'])
@token_required
def my_availability(current_user):
    db = get_db()
    rows = db.execute('''
        SELECT a.*, e.title as event_title, e.week_start, e.week_end
        FROM availability a
        JOIN events e ON a.event_id = e.id
        WHERE a.user_id = ?
        ORDER BY e.week_start DESC, a.date
    ''', (current_user['id'],)).fetchall()
    return jsonify([dict(r) for r in rows])
