from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db
from auth_utils import token_required, client_ip
import jwt
import datetime

auth_bp = Blueprint('auth', __name__)

# ── Field length limits ───────────────────────────────────────────
MAX_USERNAME = 32
MAX_EMAIL    = 254
MAX_PASSWORD = 128

# ── Brute-force protection ────────────────────────────────────────
MAX_FAILED_LOGINS    = 10
LOGIN_WINDOW_MINUTES = 15


def _rate_limit_login(ip):
    """Block an IP after too many recent failed attempts. Returns (allowed, wait_seconds)."""
    db = get_db()
    # The window must be computed by SQLite: attempted_at is written by
    # CURRENT_TIMESTAMP ("YYYY-MM-DD HH:MM:SS"), which does not compare
    # correctly against a Python isoformat string ("YYYY-MM-DDTHH:MM:SS.ffffff").
    count = db.execute(
        "SELECT COUNT(*) FROM login_attempts "
        "WHERE ip = ? AND attempted_at > datetime('now', ?)",
        (ip, f'-{LOGIN_WINDOW_MINUTES} minutes')
    ).fetchone()[0]
    if count >= MAX_FAILED_LOGINS:
        return False, LOGIN_WINDOW_MINUTES * 60
    return True, 0


def _record_failed_login(ip):
    db = get_db()
    db.execute('INSERT INTO login_attempts (ip) VALUES (?)', (ip,))
    # Clean up old attempts older than 1 hour
    db.execute("DELETE FROM login_attempts WHERE attempted_at < datetime('now', '-1 hour')")
    db.commit()


def _clear_login_attempts(ip):
    db = get_db()
    db.execute('DELETE FROM login_attempts WHERE ip = ?', (ip,))
    db.commit()


@auth_bp.route('/login', methods=['POST'])
def login():
    ip = client_ip()

    allowed, wait = _rate_limit_login(ip)
    if not allowed:
        return jsonify({'error': 'Too many failed attempts. Try again in 15 minutes.'}), 429

    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Username and password required'}), 400

    # Sanitise input lengths
    username = str(data['username'])[:MAX_USERNAME]
    password = str(data['password'])[:MAX_PASSWORD]

    db = get_db()
    user = db.execute(
        'SELECT * FROM users WHERE username = ? AND is_active = 1', (username,)
    ).fetchone()

    if not user or not check_password_hash(user['password_hash'], password):
        _record_failed_login(ip)
        # Generic message — don't reveal whether username exists
        return jsonify({'error': 'Invalid credentials'}), 401

    _clear_login_attempts(ip)

    token = jwt.encode({
        'user_id': user['id'],
        'exp': datetime.datetime.utcnow() + datetime.timedelta(days=7)
    }, current_app.config['SECRET_KEY'], algorithm='HS256')

    return jsonify({
        'token': token,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'email': user['email'],
            'role': user['role']
        }
    })


@auth_bp.route('/logout', methods=['POST'])
@token_required
def logout(current_user):
    """Blocklist the current token so it can't be reused."""
    auth_header = request.headers.get('Authorization', '')
    token = auth_header.split(' ')[1] if auth_header.startswith('Bearer ') else None
    if token:
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            jti = f"{data['user_id']}:{data['exp']}"
            db = get_db()
            db.execute('INSERT OR IGNORE INTO token_blocklist (jti) VALUES (?)', (jti,))
            # Clean up expired tokens from blocklist (older than 8 days)
            db.execute("DELETE FROM token_blocklist WHERE blocked_at < datetime('now', '-8 days')")
            db.commit()
        except Exception:
            pass
    return jsonify({'message': 'Logged out'})


@auth_bp.route('/me', methods=['GET'])
@token_required
def me(current_user):
    return jsonify({
        'id': current_user['id'],
        'username': current_user['username'],
        'email': current_user['email'],
        'role': current_user['role'],
        'created_at': current_user['created_at']
    })


@auth_bp.route('/change-password', methods=['POST'])
@token_required
def change_password(current_user):
    data = request.get_json()
    if not data or not data.get('current_password') or not data.get('new_password'):
        return jsonify({'error': 'Current and new password required'}), 400

    if not check_password_hash(current_user['password_hash'], data['current_password']):
        return jsonify({'error': 'Current password incorrect'}), 401

    new_pw = str(data['new_password'])[:MAX_PASSWORD]
    if len(new_pw) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    db = get_db()
    db.execute(
        'UPDATE users SET password_hash = ? WHERE id = ?',
        (generate_password_hash(new_pw), current_user['id'])
    )
    db.commit()
    return jsonify({'message': 'Password updated'})
