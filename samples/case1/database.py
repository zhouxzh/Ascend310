import sqlite3
import os
from datetime import datetime

DB_NAME = 'attendance.db'

def init_db():
    conn = sqlite3.connect(DB_NAME)
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

def add_user(name, embedding, avatar=None):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('INSERT INTO users (name, embedding, avatar) VALUES (?, ?, ?)', (name, embedding, avatar))
    user_id = c.lastrowid
    conn.commit()
    conn.close()
    return user_id

def update_user_name(user_id, name):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('UPDATE users SET name = ? WHERE id = ?', (name, user_id))
    conn.commit()
    conn.close()

def get_users():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute('SELECT * FROM users')
    users = [dict(row) for row in c.fetchall()]
    conn.close()
    return users

def delete_user(user_id):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('DELETE FROM users WHERE id = ?', (user_id,))
    c.execute('DELETE FROM attendance WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def add_attendance(user_id, checkin_type, image_path):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute('INSERT INTO attendance (user_id, type, image_path) VALUES (?, ?, ?)', 
              (user_id, checkin_type, image_path))
    conn.commit()
    conn.close()

def get_attendance():
    conn = sqlite3.connect(DB_NAME)
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
