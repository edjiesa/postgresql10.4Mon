import sqlite3
import json
import os

# SQLite database path (configurable via environment variable)
DB_PATH = os.environ.get(
    "DATABASE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "postgresql_mon.db")
)

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    # Ensure folder containing database exists
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Monitored databases table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS monitored_databases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        host TEXT NOT NULL,
        port INTEGER NOT NULL DEFAULT 5432,
        username TEXT NOT NULL,
        password TEXT NOT NULL,
        dbname TEXT NOT NULL,
        sslmode TEXT NOT NULL DEFAULT 'prefer',
        slow_query_threshold INTEGER NOT NULL DEFAULT 5,
        max_conn_threshold INTEGER NOT NULL DEFAULT 80,
        check_interval INTEGER NOT NULL DEFAULT 30,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 2. Alert settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS alert_settings (
        channel TEXT PRIMARY KEY,
        config TEXT NOT NULL, -- JSON string
        is_enabled INTEGER NOT NULL DEFAULT 0
    )
    """)
    
    # Initialize default alert channels if they don't exist
    for channel in ["telegram", "discord", "slack", "n8n"]:
        cursor.execute("SELECT 1 FROM alert_settings WHERE channel = ?", (channel,))
        if not cursor.fetchone():
            default_config = {}
            if channel == "telegram":
                default_config = {"bot_token": "", "chat_id": ""}
            elif channel in ["discord", "slack", "n8n"]:
                default_config = {"webhook_url": ""}
            cursor.execute(
                "INSERT INTO alert_settings (channel, config, is_enabled) VALUES (?, ?, 0)",
                (channel, json.dumps(default_config))
            )
            
    # 3. Alert logs table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS alert_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        database_id INTEGER,
        database_name TEXT,
        alert_type TEXT NOT NULL, -- 'slow_query', 'blocking_lock', 'connection_limit', 'offline'
        severity TEXT NOT NULL, -- 'info', 'warning', 'critical'
        message TEXT NOT NULL,
        details TEXT, -- JSON string
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    conn.commit()
    conn.close()

# Monitored Databases CRUD
def add_database(name, host, port, username, password, dbname, sslmode='prefer', slow_query_threshold=5, max_conn_threshold=80, check_interval=30, is_active=1):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO monitored_databases (name, host, port, username, password, dbname, sslmode, slow_query_threshold, max_conn_threshold, check_interval, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (name, host, port, username, password, dbname, sslmode, slow_query_threshold, max_conn_threshold, check_interval, is_active))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_databases():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM monitored_databases ORDER BY name ASC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_database(db_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM monitored_databases WHERE id = ?", (db_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_database(db_id, name, host, port, username, password, dbname, sslmode, slow_query_threshold, max_conn_threshold, check_interval, is_active):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE monitored_databases 
        SET name = ?, host = ?, port = ?, username = ?, password = ?, dbname = ?, sslmode = ?, 
            slow_query_threshold = ?, max_conn_threshold = ?, check_interval = ?, is_active = ?
        WHERE id = ?
    """, (name, host, port, username, password, dbname, sslmode, slow_query_threshold, max_conn_threshold, check_interval, is_active, db_id))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0

def delete_database(db_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM monitored_databases WHERE id = ?", (db_id,))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0

# Alert Settings CRUD
def get_all_alert_settings():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alert_settings")
    rows = cursor.fetchall()
    conn.close()
    res = {}
    for r in rows:
        res[r['channel']] = {
            "config": json.loads(r['config']),
            "is_enabled": bool(r['is_enabled'])
        }
    return res

def get_alert_settings(channel):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alert_settings WHERE channel = ?", (channel,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {
            "config": json.loads(row['config']),
            "is_enabled": bool(row['is_enabled'])
        }
    return None

def update_alert_settings(channel, config_dict, is_enabled):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE alert_settings 
        SET config = ?, is_enabled = ?
        WHERE channel = ?
    """, (json.dumps(config_dict), 1 if is_enabled else 0, channel))
    conn.commit()
    conn.close()
    return cursor.rowcount > 0

# Alert Logs CRUD
def add_alert_log(database_id, database_name, alert_type, severity, message, details_dict=None):
    conn = get_db_connection()
    cursor = conn.cursor()
    details_str = json.dumps(details_dict) if details_dict else None
    cursor.execute("""
        INSERT INTO alert_logs (database_id, database_name, alert_type, severity, message, details)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (database_id, database_name, alert_type, severity, message, details_str))
    conn.commit()
    new_id = cursor.lastrowid
    conn.close()
    return new_id

def get_alert_logs(limit=100):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM alert_logs ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    result = []
    for r in rows:
        d = dict(r)
        if d['details']:
            try:
                d['details'] = json.loads(d['details'])
            except Exception:
                pass
        result.append(d)
    return result

def clear_alert_logs():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM alert_logs")
    conn.commit()
    conn.close()
