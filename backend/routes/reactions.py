from flask import Blueprint, request, jsonify
from database import get_db
from auth_utils import token_required

reactions_bp = Blueprint('reactions', __name__)


@reactions_bp.route('/event/<int:event_id>', methods=['GET'])
@token_required
def get_reactions(current_user, event_id):
    db = get_db()
    rows = db.execute('''
        SELECT r.*, u.username as reactor_username
        FROM reactions r
        JOIN users u ON r.user_id = u.id
        WHERE r.event_id = ?
    ''', (event_id,)).fetchall()
    return jsonify([dict(r) for r in rows])


@reactions_bp.route('/event/<int:event_id>/set', methods=['POST'])
@token_required
def set_reaction(current_user, event_id):
    data = request.get_json()
    if not data or not data.get('date') or not data.get('target_user_id') or not data.get('emoji'):
        return jsonify({'error': 'date, target_user_id, and emoji required'}), 400

    date = data['date']
    target_user_id = int(data['target_user_id'])
    emoji = data['emoji'].strip()

    db = get_db()

    # Check if user already has a reaction here
    existing = db.execute(
        'SELECT emoji FROM reactions WHERE user_id = ? AND event_id = ? AND date = ? AND target_user_id = ?',
        (current_user['id'], event_id, date, target_user_id)
    ).fetchone()

    if existing and existing['emoji'] == emoji:
        # Same emoji — remove it (toggle off)
        db.execute(
            'DELETE FROM reactions WHERE user_id = ? AND event_id = ? AND date = ? AND target_user_id = ?',
            (current_user['id'], event_id, date, target_user_id)
        )
        db.commit()
        return jsonify({'action': 'removed'})
    else:
        # New or different emoji — upsert
        db.execute('''
            INSERT INTO reactions (user_id, event_id, date, target_user_id, emoji)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, event_id, date, target_user_id)
            DO UPDATE SET emoji = excluded.emoji, created_at = CURRENT_TIMESTAMP
        ''', (current_user['id'], event_id, date, target_user_id, emoji))
        db.commit()
        return jsonify({'action': 'set', 'emoji': emoji})
