from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS
from flask_limiter import Limiter
from database import init_db
from auth_utils import client_ip
import secret_key as secret_key_mod
from routes.auth import auth_bp
from routes.events import events_bp
from routes.availability import availability_bp
from routes.admin import admin_bp
from routes.discord import discord_bp, start_scheduler
from routes.signal import signal_bp, start_signal_scheduler
from routes.recurring import recurring_bp, start_recurring_scheduler
import os
import sys

# ── Signing key ───────────────────────────────────────────────────
# SECRET_KEY from the environment wins; otherwise one is generated and kept
# in the data volume so a fresh install just works. See secret_key.py.
DATABASE_PATH = os.environ.get('DATABASE_PATH', '/data/calendar.db')
SECRET_KEY, SECRET_KEY_SOURCE = secret_key_mod.resolve(database_path=DATABASE_PATH)
print(secret_key_mod.describe(SECRET_KEY_SOURCE))
if SECRET_KEY_SOURCE != 'environment' and not secret_key_mod.key_is_private(SECRET_KEY_SOURCE):
    print(f'WARNING: {SECRET_KEY_SOURCE} is readable by other users on the host.', file=sys.stderr)

# In the image the UI is copied to backend/static; running from a checkout
# it still lives at frontend/index.html. Serve whichever is present.
_HERE = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_HERE, 'static')
if not os.path.isfile(os.path.join(_STATIC_DIR, 'index.html')):
    _STATIC_DIR = os.path.join(os.path.dirname(_HERE), 'frontend')

app = Flask(__name__, static_folder=_STATIC_DIR, static_url_path='')
app.config['SECRET_KEY'] = SECRET_KEY
app.config['DATABASE'] = DATABASE_PATH

CORS(app, resources={r"/api/*": {"origins": "*"}})

# ── Rate limiting ─────────────────────────────────────────────────
# Keyed on the forwarded client IP so users behind one reverse proxy don't
# share a single bucket. This is a coarse request-volume cap; the per-account
# brute-force protection lives in routes/auth.py.
limiter = Limiter(
    key_func=client_ip,
    app=app,
    default_limits=[],
    storage_uri='memory://'
)
app.config['LIMITER'] = limiter
limiter.limit('30 per minute')(auth_bp)

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
app.register_blueprint(recurring_bp,     url_prefix='/api/recurring')

start_scheduler(app)
start_signal_scheduler(app)
start_recurring_scheduler(app)

@app.route('/api/health')
def health():
    return {'status': 'ok'}

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve_frontend(path):
    return send_from_directory(app.static_folder, 'index.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=os.environ.get('FLASK_DEBUG', 'false').lower() == 'true')
