from flask import Blueprint, request, jsonify, current_app
from werkzeug.security import generate_password_hash, check_password_hash
from database import get_db
from auth_utils import token_required
import jwt
import datetime

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    if not data or not data.get('username') or not data.get('password'):
        return jsonify({'error': 'Username and password required'}), 400

    db = get_db()
    user = db.execute(
        'SELECT * FROM users WHERE username = ? AND is_active = 1', (data['username'],)
    ).fetchone()

    if not user or not check_password_hash(user['password_hash'], data['password']):
        return jsonify({'error': 'Invalid credentials'}), 401

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


@auth_bp.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    required = ['username', 'email', 'password']
    if not data or not all(k in data for k in required):
        return jsonify({'error': 'Username, email, and password required'}), 400

    if len(data['password']) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    db = get_db()
    try:
        db.execute(
            'INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)',
            (data['username'].strip(), data['email'].strip(),
             generate_password_hash(data['password']), 'user')
        )
        db.commit()
    except Exception as e:
        return jsonify({'error': 'Username or email already taken'}), 409

    return jsonify({'message': 'Account created successfully'}), 201


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

    if len(data['new_password']) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400

    db = get_db()
    db.execute(
        'UPDATE users SET password_hash = ? WHERE id = ?',
        (generate_password_hash(data['new_password']), current_user['id'])
    )
    db.commit()
    return jsonify({'message': 'Password updated'})
