from flask import Flask, send_from_directory
from flask_cors import CORS
from database import init_db
from routes.auth import auth_bp
from routes.events import events_bp
from routes.availability import availability_bp
from routes.admin import admin_bp
from routes.discord import discord_bp, start_scheduler
from routes.signal import signal_bp, start_signal_scheduler
from routes.reactions import reactions_bp
import os

app = Flask(__name__, static_folder='static', static_url_path='')
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-me-in-production')
app.config['DATABASE'] = os.environ.get('DATABASE_PATH', '/data/calendar.db')

CORS(app, resources={r"/api/*": {"origins": "*"}})

init_db(app)

app.register_blueprint(auth_bp,          url_prefix='/api/auth')
app.register_blueprint(events_bp,        url_prefix='/api/events')
app.register_blueprint(availability_bp,  url_prefix='/api/availability')
app.register_blueprint(admin_bp,         url_prefix='/api/admin')
app.register_blueprint(discord_bp,       url_prefix='/api/discord')
app.register_blueprint(signal_bp,        url_prefix='/api/signal')
app.register_blueprint(reactions_bp,     url_prefix='/api/reactions')

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
