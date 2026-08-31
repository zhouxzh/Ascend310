import sqlite3
import threading
from functools import wraps
from datetime import datetime

from .config import DB_PATH

DB_NAME = str(DB_PATH)
DB_LOCK = threading.RLock()


def _serialized(func):
    """Serialize SQLite operations within this single-worker process."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        with DB_LOCK:
            return func(*args, **kwargs)

    return wrapper


def _connect():
    conn = sqlite3.connect(DB_NAME, timeout=5.0)
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn

@_serialized
def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect()
    c = conn.cursor()

    # User table
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            embedding BLOB,
            avatar TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Attendance table
    c.execute('''
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            type TEXT,
            image_path TEXT,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')

    conn.commit()
    conn.close()

@_serialized
def add_user(name, embedding, avatar=None):
    conn = _connect()
    c = conn.cursor()
    c.execute('INSERT INTO users (name, embedding, avatar) VALUES (?, ?, ?)', (name, embedding, avatar))
    user_id = c.lastrowid
    conn.commit()
    conn.close()
    return user_id

@_serialized
def update_user_name(user_id, name):
    conn = _connect()
    c = conn.cursor()
    c.execute('UPDATE users SET name = ? WHERE id = ?', (name, user_id))
    conn.commit()
    conn.close()

@_serialized
def get_users():
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM users')
    users = [dict(row) for row in c.fetchall()]
    conn.close()
    return users

@_serialized
def delete_user(user_id):
    conn = _connect()
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE id = ?', (user_id,))
    c.execute('DELETE FROM attendance WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

@_serialized
def add_attendance(user_id, checkin_type, image_path):
    conn = _connect()
    c = conn.cursor()
    c.execute('INSERT INTO attendance (user_id, type, image_path) VALUES (?, ?, ?)',
              (user_id, checkin_type, image_path))
    conn.commit()
    conn.close()

@_serialized
def get_attendance():
    conn = _connect()
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('''
        SELECT a.*, u.name
        FROM attendance a
        LEFT JOIN users u ON a.user_id = u.id
        WHERE a.id IN (
            SELECT MAX(id)
            FROM attendance
            WHERE DATE(timestamp) = DATE('now', 'localtime')
            GROUP BY user_id
        )
        ORDER BY a.timestamp DESC
    ''')
    records = [dict(row) for row in c.fetchall()]
    conn.close()
    return records

if __name__ == '__main__':
    init_db()
    print("Database initialized.")
