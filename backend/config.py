import psycopg
from psycopg.rows import dict_row
import json
import os
import time

def get_db_connection():
    """
    Creates a connection to the application configuration PostgreSQL database.
    Reads credentials from environment variables.
    """
    host = os.environ.get("APP_DB_HOST", "localhost")
    port = os.environ.get("APP_DB_PORT", "5432")
    dbname = os.environ.get("APP_DB_NAME", "pg_mon")
    user = os.environ.get("APP_DB_USER", "pg_mon")
    password = os.environ.get("APP_DB_PASS", "pg_mon_pass")
    
    conn_str = f"host={host} port={port} dbname={dbname} user={user} password={password} connect_timeout=10"
    
    # Try connecting. In a container startup flow, database may take some seconds to boot.
    # We will allow a retry window if connection fails.
    for attempt in range(5):
        try:
            conn = psycopg.connect(conn_str, row_factory=dict_row)
            return conn
        except Exception as e:
            if attempt == 4:
                raise e
            time.sleep(2)

def init_db():
    """
    Creates tables inside application config PostgreSQL database if they do not exist.
    Also registers default alert channels.
    """
    conn = get_db_connection()
    with conn.cursor() as cursor:
        # 1. Monitored databases table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS monitored_databases (
            id SERIAL PRIMARY KEY,
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
        for channel in ["telegram", "discord", "slack", "n8n", "google_chat"]:
            cursor.execute("SELECT 1 FROM alert_settings WHERE channel = %s", (channel,))
            if not cursor.fetchone():
                default_config = {}
                if channel == "telegram":
                    default_config = {"bot_token": "", "chat_id": ""}
                elif channel in ["discord", "slack", "n8n", "google_chat"]:
                    default_config = {"webhook_url": ""}
                cursor.execute(
                    "INSERT INTO alert_settings (channel, config, is_enabled) VALUES (%s, %s, 0)",
                    (channel, json.dumps(default_config))
                )
                
        # 3. Alert logs table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS alert_logs (
            id SERIAL PRIMARY KEY,
            database_id INTEGER,
            database_name TEXT,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL,
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
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO monitored_databases (name, host, port, username, password, dbname, sslmode, slow_query_threshold, max_conn_threshold, check_interval, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (name, host, port, username, password, dbname, sslmode, slow_query_threshold, max_conn_threshold, check_interval, is_active))
        new_id = cursor.fetchone()['id']
    conn.commit()
    conn.close()
    return new_id

def get_databases():
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM monitored_databases ORDER BY name ASC")
        rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_database(db_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM monitored_databases WHERE id = %s", (db_id,))
        row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_database(db_id, name, host, port, username, password, dbname, sslmode, slow_query_threshold, max_conn_threshold, check_interval, is_active):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE monitored_databases 
            SET name = %s, host = %s, port = %s, username = %s, password = %s, dbname = %s, sslmode = %s, 
                slow_query_threshold = %s, max_conn_threshold = %s, check_interval = %s, is_active = %s
            WHERE id = %s
        """, (name, host, port, username, password, dbname, sslmode, slow_query_threshold, max_conn_threshold, check_interval, is_active, db_id))
        rowcount = cursor.rowcount
    conn.commit()
    conn.close()
    return rowcount > 0

def delete_database(db_id):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM monitored_databases WHERE id = %s", (db_id,))
        rowcount = cursor.rowcount
    conn.commit()
    conn.close()
    return rowcount > 0

# Alert Settings CRUD
def get_all_alert_settings():
    conn = get_db_connection()
    with conn.cursor() as cursor:
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
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM alert_settings WHERE channel = %s", (channel,))
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
    with conn.cursor() as cursor:
        cursor.execute("""
            UPDATE alert_settings 
            SET config = %s, is_enabled = %s
            WHERE channel = %s
        """, (json.dumps(config_dict), 1 if is_enabled else 0, channel))
        rowcount = cursor.rowcount
    conn.commit()
    conn.close()
    return rowcount > 0

# Alert Logs CRUD
def add_alert_log(database_id, database_name, alert_type, severity, message, details_dict=None):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        details_str = json.dumps(details_dict) if details_dict else None
        cursor.execute("""
            INSERT INTO alert_logs (database_id, database_name, alert_type, severity, message, details)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (database_id, database_name, alert_type, severity, message, details_str))
        new_id = cursor.fetchone()['id']
    conn.commit()
    conn.close()
    return new_id

def get_alert_logs(limit=100):
    conn = get_db_connection()
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM alert_logs ORDER BY created_at DESC LIMIT %s", (limit,))
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
    with conn.cursor() as cursor:
        cursor.execute("DELETE FROM alert_logs")
    conn.commit()
    conn.close()
