import sqlite3
import click
from flask import current_app, g
from werkzeug.security import generate_password_hash


def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(
            current_app.config['DATABASE'],
            detect_types=sqlite3.PARSE_DECLTYPES
        )
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(e=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db(app):
    app.teardown_appcontext(close_db)

    with app.app_context():
        db = get_db()
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                week_start TEXT NOT NULL,
                week_end TEXT NOT NULL,
                created_by INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS availability (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                event_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'available',
                note TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, event_id, date),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS discord_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                message_type TEXT NOT NULL,
                date TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success INTEGER NOT NULL DEFAULT 1,
                error TEXT,
                FOREIGN KEY (event_id) REFERENCES events(id)
            );

            CREATE TABLE IF NOT EXISTS token_blocklist (
                jti TEXT PRIMARY KEY,
                blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS login_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT NOT NULL,
                attempted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS signal_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER,
                message_type TEXT NOT NULL,
                date TEXT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                success INTEGER NOT NULL DEFAULT 1,
                error TEXT,
                FOREIGN KEY (event_id) REFERENCES events(id)
            );
        ''')

        # Create default admin if none exists
        existing = db.execute('SELECT id FROM users WHERE role = ?', ('admin',)).fetchone()
        if not existing:
            db.execute(
                'INSERT INTO users (username, email, password_hash, role) VALUES (?, ?, ?, ?)',
                ('admin', 'admin@local.host', generate_password_hash('admin123'), 'admin')
            )
            db.commit()
            print('Default admin created: admin / admin123')
