from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from database import init_db
from routes.auth import auth_bp
from routes.events import events_bp
from routes.availability import availability_bp
from routes.admin import admin_bp
from routes.discord import discord_bp, start_scheduler
from routes.signal import signal_bp, start_signal_scheduler
import os
import sys

# ── Startup security check ────────────────────────────────────────
SECRET_KEY = os.environ.get('SECRET_KEY', 'change-me-in-production')
if SECRET_KEY == 'change-me-in-production':
    print('FATAL: SECRET_KEY is set to the default value. Set a strong SECRET_KEY environment variable.', file=sys.stderr)
    sys.exit(1)

app = Flask(__name__, static_folder='static', static_url_path='')
app.config['SECRET_KEY'] = SECRET_KEY
app.config['DATABASE'] = os.environ.get('DATABASE_PATH', '/data/calendar.db')

CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── Rate limiting ─────────────────────────────────────────────────
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=[],
    storage_uri='memory://'
)
app.config['LIMITER'] = limiter

# ── Security headers ──────────────────────────────────────────────
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), microphone=(), camera=()'
    return response

init_db(app)

app.register_blueprint(auth_bp,          url_prefix='/api/auth')
app.register_blueprint(events_bp,        url_prefix='/api/events')
app.register_blueprint(availability_bp,  url_prefix='/api/availability')
app.register_blueprint(admin_bp,         url_prefix='/api/admin')
app.register_blueprint(discord_bp,       url_prefix='/api/discord')
app.register_blueprint(signal_bp,        url_prefix='/api/signal')

start_scheduler(app)
start_signal_scheduler(app)

@app.route('/api/health')
def health():
    return {'status': 'ok'}

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true')
