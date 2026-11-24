from flask import Blueprint, request, jsonify
from werkzeug.security import generate_password_hash
from database import get_db
from auth_utils import admin_required
import access_sso
import re

admin_bp = Blueprint('admin', __name__)

MAX_USERNAME = 32
MAX_EMAIL    = 254
MAX_PASSWORD = 128

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def _validate_user_fields(username=None, email=None, password=None):
    """Returns error string or None."""
    if username is not None:
        if not username or len(username) > MAX_USERNAME:
            return f'Username must be 1–{MAX_USERNAME} characters'
        if not re.match(r'^[a-zA-Z0-9_\-\.]+$', username):
            return 'Username may only contain letters, numbers, underscores, hyphens, dots'
    if email is not None:
        if not email or len(email) > MAX_EMAIL or not EMAIL_RE.match(email):
            return 'Invalid email address'
    if password is not None:
        if len(password) < 6:
            return 'Password must be at least 6 characters'
        if len(password) > MAX_PASSWORD:
            return f'Password must be at most {MAX_PASSWORD} characters'
    return None


@admin_bp.route('/users', methods=['GET'])
@admin_required
def get_users(current_user):
    db = get_db()
    users = db.execute(
        'SELECT id, username, email, role, is_active, created_at, signal_number FROM users ORDER BY username'
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

    if 'email' in data:
        email = str(data['email']).strip()
        err = _validate_user_fields(email=email)
        if err:
            return jsonify({'error': err}), 400
        taken = db.execute(
            'SELECT 1 FROM users WHERE lower(email) = lower(?) AND id != ?', (email, user_id)
        ).fetchone()
        if taken:
            return jsonify({'error': 'Another user already has that email'}), 409
        updates.append('email = ?')
        params.append(email)

    if 'signal_number' in data:
        num = str(data['signal_number']).strip()[:20]
        if num and not re.match(r'^\+?[0-9]{7,20}$', num):
            return jsonify({'error': 'Signal number must be digits, optionally starting with +'}), 400
        updates.append('signal_number = ?')
        params.append(num or None)

    if 'password' in data and data['password']:
        err = _validate_user_fields(password=str(data['password']))
        if err:
            return jsonify({'error': err}), 400
        updates.append('password_hash = ?')
        params.append(generate_password_hash(str(data['password'])[:MAX_PASSWORD]))

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

    username = str(data['username']).strip()
    email    = str(data['email']).strip()
    password = str(data['password'])

    err = _validate_user_fields(username=username, email=email, password=password)
    if err:
        return jsonify({'error': err}), 400

    role = data.get('role', 'user')
    if role not in ('admin', 'user'):
        role = 'user'

    signal_number = str(data.get('signal_number', '')).strip()[:20]
    if signal_number and not re.match(r'^\+?[0-9]{7,20}$', signal_number):
        return jsonify({'error': 'Signal number must be digits, optionally starting with +'}), 400

    db = get_db()
    # The UNIQUE index on email is case-sensitive, but single sign-on matches
    # on lower(email) — so two accounts differing only in case would make that
    # lookup ambiguous. Guard it here the same way update_user does.
    taken = db.execute('SELECT 1 FROM users WHERE lower(email) = lower(?)', (email,)).fetchone()
    if taken:
        return jsonify({'error': 'Another user already has that email'}), 409

    try:
        db.execute(
            'INSERT INTO users (username, email, password_hash, role, signal_number) VALUES (?, ?, ?, ?, ?)',
            (username, email, generate_password_hash(password), role, signal_number or None)
        )
        db.commit()
    except Exception:
        return jsonify({'error': 'Username or email already taken'}), 409

    return jsonify({'message': 'User created'}), 201


# ── Single sign-on (Cloudflare Access) ───────────────────────────

def _sso_status():
    team, aud = access_sso.raw_settings()
    email = access_sso.verified_email(request)
    matched = None
    if email:
        row = get_db().execute(
            'SELECT username FROM users WHERE lower(email) = ? AND is_active = 1', (email,)
        ).fetchone()
        matched = row['username'] if row else None
    return {
        'team_domain': team,
        'aud': aud,
        'enabled': access_sso.enabled(),
        'env_locked': access_sso.env_locked(),
        # What this very request looked like, so the admin can see whether it works.
        'header_seen': bool(request.headers.get('Cf-Access-Jwt-Assertion')),
        'access_email': email,
        'matched_user': matched,
    }


@admin_bp.route('/sso', methods=['GET'])
@admin_required
def get_sso_settings(current_user):
    return jsonify(_sso_status())


@admin_bp.route('/sso', methods=['POST'])
@admin_required
def save_sso_settings(current_user):
    if access_sso.env_locked():
        return jsonify({'error': 'Single sign-on is set by the CF_ACCESS_TEAM_DOMAIN / CF_ACCESS_AUD '
                                 'environment variables. Change them there, or remove them to manage it here.'}), 400
    data = request.get_json() or {}
    try:
        team = access_sso.clean_team(data.get('team_domain'))
        aud = access_sso.clean_aud(data.get('aud'))
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if bool(team) != bool(aud):
        return jsonify({'error': 'Fill in both the team domain and the AUD tag, or clear both to turn single sign-on off.'}), 400
    if team:
        try:
            access_sso.check_team_keys(team)
        except Exception as e:
            return jsonify({'error': f"Couldn't load the signing keys from {team} ({e}). Check the team domain."}), 400
    db = get_db()
    for key, val in (('cf_access_team_domain', team), ('cf_access_aud', aud)):
        db.execute(
            'INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
            (key, val)
        )
    db.commit()
    return jsonify(_sso_status())
