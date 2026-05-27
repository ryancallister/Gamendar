from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash
from database import get_db
from auth_utils import admin_required

admin_bp = Blueprint('admin', __name__)


@admin_bp.route('/users', methods=['GET'])
@admin_required
def get_users(current_user):
    db = get_db()
    users = db.execute(
        'SELECT id, username, email, role, is_active, created_at FROM users ORDER BY username'
    ).fetchall()
    return jsonify([dict(u) for u in users])


@admin_bp.route('/users/<int:user_id>', methods=['PUT'])
@admin_required
def update_user(current_user, user_id):
    data = request.get_json()
    db = get_db()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    if not user:
        return jsonify({'error': 'User not found'}), 404

    # Prevent demoting self
    if user_id == current_user['id'] and data.get('role') == 'user':
        return jsonify({'error': 'Cannot demote yourself'}), 400

    updates = []
    params = []

    if 'role' in data and data['role'] in ('admin', 'user'):
        updates.append('role = ?')
        params.append(data['role'])

    if 'is_active' in data:
        updates.append('is_active = ?')
        params.append(1 if data['is_active'] else 0)

    if 'password' in data and data['password']:
        if len(data['password']) < 6:
            return jsonify({'error': 'Password must be at least 6 characters'}), 400
        updates.append('password_hash = ?')
        params.append(generate_password_hash(data['password']))

    if not updates:
        return jsonify({'error': 'Nothing to update'}), 400

    params.append(user_id)
    db.execute(f'UPDATE users SET {", ".join(updates)} WHERE id = ?', params)
    db.commit()
    return jsonify({'message': 'User updated'})


@admin_bp.route('/users/<int:user_id>', methods=['DELETE'])
@admin_required
def delete_user(current_user, user_id):
    if user_id == current_user['id']:
        return jsonify({'error': 'Cannot delete yourself'}), 400
    db = get_db()
    db.execute('DELETE FROM availability WHERE user_id = ?', (user_id,))
    db.execute('DELETE FROM users WHERE id = ?', (user_id,))
    db.commit()
    return jsonify({'message': 'User deleted'})


@admin_bp.route('/users', methods=['POST'])
@admin_required
def create_user(current_user):
    data = request.get_json()
    required = ['username', 'email', 'password']
    if not data or not all(k in data for k in required):
        return jsonify({'error': 'Username, email, and password required'}), 400

    db = get_db()
    try:
        db.execute(
            'INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)',
            (data['username'].strip(), data['email'].strip(),
             generate_password_hash(data['password']), data.get('role', 'user'))
        )
        db.commit()
    except Exception:
        return jsonify({'error': 'Username or email already taken'}), 409

    return jsonify({'message': 'User created'}), 201
