import sqlite3
import json
import os
import uuid
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Route directly to the official Superset data directory which is guaranteed
# to be writable strictly by the `superset` user mapped natively in the Dockerfile.
_SUPERSET_HOME = os.environ.get('SUPERSET_HOME', os.path.expanduser('~/.superset'))
if not os.path.exists(_SUPERSET_HOME):
    try:
        os.makedirs(_SUPERSET_HOME, exist_ok=True)
    except:
        _SUPERSET_HOME = '/tmp'  # ultimate fallback

DB_PATH = os.path.join(_SUPERSET_HOME, 'chatbi_sessions.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    try:
        conn = get_connection()
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                title TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                payload TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
            )
        ''')
        conn.commit()
        conn.close()
        logger.info(f"Initialized ChatBI SQLite database at {DB_PATH}")
    except Exception as e:
        logger.error(f"Failed to initialize SQLite database: {e}")

def get_user_sessions(user_id: str) -> List[Dict[str, Any]]:
    conn = get_connection()
    c = conn.cursor()
    c.execute('SELECT id, title, updated_at FROM sessions WHERE user_id = ? ORDER BY updated_at DESC', (user_id,))
    rows = c.fetchall()
    
    sessions = []
    for r in rows:
        session_id = r['id']
        c.execute('SELECT payload FROM messages WHERE session_id = ?', (session_id,))
        msg_rows = c.fetchall()
        
        messages = []
        for m in msg_rows:
            try:
                messages.append(json.loads(m['payload']))
            except:
                pass
                
        # Sort messages basically by timestamp if present
        messages.sort(key=lambda x: x.get('timestamp', 0))
                
        sessions.append({
            "id": session_id,
            "title": r['title'],
            "updatedAt": r['updated_at'],
            "messages": messages
        })
    conn.close()
    return sessions

def save_session(user_id: str, session_data: Dict[str, Any]):
    conn = get_connection()
    c = conn.cursor()
    
    session_id = session_data['id']
    title = session_data['title']
    updated_at = session_data['updatedAt']
    messages = session_data.get('messages', [])
    
    c.execute('SELECT id FROM sessions WHERE id = ?', (session_id,))
    if c.fetchone():
        c.execute('UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?', (title, updated_at, session_id))
    else:
        c.execute('INSERT INTO sessions (id, user_id, title, updated_at) VALUES (?, ?, ?, ?)', 
                  (session_id, user_id, title, updated_at))
    
    # Safest sync mechanism: overwrite all messages for this session
    c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
    
    for m in messages:
        msg_id = m.get('id', str(uuid.uuid4()))
        c.execute('INSERT INTO messages (id, session_id, payload) VALUES (?, ?, ?)',
                  (msg_id, session_id, json.dumps(m)))
                  
    conn.commit()
    conn.close()

def delete_session(user_id: str, session_id: str):
    conn = get_connection()
    c = conn.cursor()
    # Double check user_id owns this session
    c.execute('SELECT id FROM sessions WHERE id = ? AND user_id = ?', (session_id, user_id))
    if c.fetchone():
        c.execute('DELETE FROM messages WHERE session_id = ?', (session_id,))
        c.execute('DELETE FROM sessions WHERE id = ?', (session_id,))
        conn.commit()
    conn.close()

# Auto-initialize
init_db()
