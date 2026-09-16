import jwt
import functools
from flask import request, jsonify, current_app
from database import get_db


def client_ip():
    """Best-effort originating IP.

    Assumes a trusted reverse proxy (Cloudflare, nginx) sits in front; the
    forwarded headers are client-supplied and must not be trusted when the
    app is exposed directly.
    """
    return (
        request.headers.get('CF-Connecting-IP')
        or request.headers.get('X-Forwarded-For', '').split(',')[0].strip()
        or request.remote_addr
        or 'unknown'
    )


def _is_token_blocked(db, user_id, exp):
    jti = f"{user_id}:{exp}"
    row = db.execute('SELECT jti FROM token_blocklist WHERE jti = ?', (jti,)).fetchone()
    return row is not None


def token_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        if not token:
            return jsonify({'error': 'Token missing'}), 401
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            db = get_db()
            # Check blocklist
            if _is_token_blocked(db, data['user_id'], data['exp']):
                return jsonify({'error': 'Token has been revoked'}), 401
            current_user = db.execute(
                'SELECT * FROM users WHERE id = ? AND is_active = 1', (data['user_id'],)
            ).fetchone()
            if not current_user:
                return jsonify({'error': 'User not found or inactive'}), 401
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(current_user, *args, **kwargs)
    return decorated


def admin_required(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        token = None
        auth_header = request.headers.get('Authorization', '')
        if auth_header.startswith('Bearer '):
            token = auth_header.split(' ')[1]
        if not token:
            return jsonify({'error': 'Token missing'}), 401
        try:
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            db = get_db()
            if _is_token_blocked(db, data['user_id'], data['exp']):
                return jsonify({'error': 'Token has been revoked'}), 401
            current_user = db.execute(
                'SELECT * FROM users WHERE id = ? AND is_active = 1', (data['user_id'],)
            ).fetchone()
            if not current_user:
                return jsonify({'error': 'User not found'}), 401
            if current_user['role'] != 'admin':
                return jsonify({'error': 'Admin access required'}), 403
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Invalid token'}), 401
        return f(current_user, *args, **kwargs)
    return decorated
