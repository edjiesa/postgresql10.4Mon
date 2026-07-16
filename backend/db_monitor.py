import psycopg
from psycopg.rows import dict_row
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_monitor")

def get_connection_string(db_config):
    """
    Construct connection string from config dictionary.
    """
    host = db_config.get('host')
    port = db_config.get('port', 5432)
    user = db_config.get('username')
    password = db_config.get('password')
    dbname = db_config.get('dbname')
    sslmode = db_config.get('sslmode', 'prefer')
    
    return f"host='{host}' port={port} user='{user}' password='{password}' dbname='{dbname}' sslmode='{sslmode}' connect_timeout=5"

def test_connection(db_config):
    """
    Test connection to a remote PostgreSQL server.
    Returns (success_boolean, message)
    """
    conn_str = get_connection_string(db_config)
    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version();")
                version = cur.fetchone()[0]
                return True, f"Connected successfully. Version: {version}"
    except Exception as e:
        logger.error(f"Connection test failed: {str(e)}")
        return False, str(e)

def check_db_metrics(db_config):
    """
    Gathers database performance metrics from remote PostgreSQL.
    Returns dictionary with all metrics, or raises an Exception.
    """
    conn_str = get_connection_string(db_config)
    metrics = {
        "status": "online",
        "pg_version": "Unknown",
        "db_size": "Unknown",
        "active_connections": 0,
        "max_connections": 100,
        "cache_hit_ratio": 0.0,
        "index_hit_ratio": 0.0,
        "slow_queries": [],
        "blocking_queries": [],
        "timestamp": time.time()
    }
    
    slow_threshold = db_config.get('slow_query_threshold', 5)
    
    with psycopg.connect(conn_str, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            # 1. Version, DB Size, Connections
            try:
                cur.execute("""
                    SELECT 
                        (SELECT version()) AS pg_version,
                        (SELECT pg_size_pretty(pg_database_size(current_database()))) AS db_size,
                        (SELECT count(*) FROM pg_stat_activity) AS active_connections,
                        (SELECT setting::int FROM pg_settings WHERE name = 'max_connections') AS max_connections;
                """)
                row = cur.fetchone()
                if row:
                    metrics["pg_version"] = row.get("pg_version", "Unknown")
                    metrics["db_size"] = row.get("db_size", "Unknown")
                    metrics["active_connections"] = row.get("active_connections", 0)
                    metrics["max_connections"] = row.get("max_connections", 100)
            except Exception as e:
                logger.warning(f"Error querying connection stats: {e}")

            # 2. Cache Hit Ratio
            try:
                cur.execute("""
                    SELECT 
                        CASE 
                            WHEN sum(blks_hit) + sum(blks_read) = 0 THEN 0.0
                            ELSE round((sum(blks_hit)::float / (sum(blks_hit) + sum(blks_read))::float) * 100, 2)
                        END AS cache_hit_ratio
                    FROM pg_stat_database;
                """)
                row = cur.fetchone()
                if row:
                    metrics["cache_hit_ratio"] = float(row.get("cache_hit_ratio") or 0.0)
            except Exception as e:
                logger.warning(f"Error querying cache hit ratio: {e}")

            # 3. Index Hit Ratio
            try:
                cur.execute("""
                    SELECT 
                        CASE 
                            WHEN sum(idx_blks_hit) + sum(idx_blks_read) = 0 THEN 0.0
                            ELSE round((sum(idx_blks_hit)::float / (sum(idx_blks_hit) + sum(idx_blks_read))::float) * 100, 2)
                        END AS index_hit_ratio
                    FROM pg_statio_all_indexes;
                """)
                row = cur.fetchone()
                if row:
                    metrics["index_hit_ratio"] = float(row.get("index_hit_ratio") or 0.0)
            except Exception as e:
                logger.warning(f"Error querying index hit ratio: {e}")

            # 4. Slow Queries
            try:
                cur.execute("""
                    SELECT 
                        pid,
                        usename AS username,
                        client_addr AS client_ip,
                        backend_start,
                        query_start,
                        state,
                        wait_event_type,
                        wait_event,
                        query,
                        round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2) AS duration_seconds
                    FROM pg_stat_activity
                    WHERE state != 'idle'
                      AND query NOT LIKE '%%pg_stat_activity%%'
                      AND (clock_timestamp() - query_start) > (%s * interval '1 second')
                    ORDER BY duration_seconds DESC;
                """, (slow_threshold,))
                rows = cur.fetchall()
                metrics["slow_queries"] = [dict(r) for r in rows]
            except Exception as e:
                logger.warning(f"Error querying slow queries: {e}")

            # 5. Blocking Locks
            try:
                cur.execute("""
                    SELECT
                        blocked_locks.pid     AS blocked_pid,
                        blocked_activity.usename  AS blocked_user,
                        blocked_activity.query    AS blocked_statement,
                        blocking_locks.pid    AS blocking_pid,
                        blocking_activity.usename AS blocking_user,
                        blocking_activity.query   AS blocking_statement,
                        round(extract(epoch from (clock_timestamp() - blocked_activity.query_start))::numeric, 2) AS blocked_duration_seconds
                    FROM pg_catalog.pg_locks         blocked_locks
                    JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.pid = blocked_locks.pid
                    JOIN pg_catalog.pg_locks         blocking_locks 
                        ON blocking_locks.locktype = blocked_locks.locktype
                        AND blocking_locks.database IS NOT DISTINCT FROM blocked_locks.database
                        AND blocking_locks.relation IS NOT DISTINCT FROM blocked_locks.relation
                        AND blocking_locks.page IS NOT DISTINCT FROM blocked_locks.page
                        AND blocking_locks.tuple IS NOT DISTINCT FROM blocked_locks.tuple
                        AND blocking_locks.virtualxid IS NOT DISTINCT FROM blocked_locks.virtualxid
                        AND blocking_locks.transactionid IS NOT DISTINCT FROM blocked_locks.transactionid
                        AND blocking_locks.classid IS NOT DISTINCT FROM blocked_locks.classid
                        AND blocking_locks.objid IS NOT DISTINCT FROM blocked_locks.objid
                        AND blocking_locks.objsubid IS NOT DISTINCT FROM blocked_locks.objsubid
                        AND blocking_locks.pid != blocked_locks.pid
                    JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.pid = blocking_locks.pid
                    WHERE NOT blocked_locks.granted;
                """)
                rows = cur.fetchall()
                metrics["blocking_queries"] = [dict(r) for r in rows]
            except Exception as e:
                logger.warning(f"Error querying blocking locks: {e}")

    return metrics

def terminate_query(db_config, pid):
    """
    Terminates a specific query execution by pid.
    Returns (success_boolean, message)
    """
    conn_str = get_connection_string(db_config)
    try:
        with psycopg.connect(conn_str) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_terminate_backend(%s);", (pid,))
                result = cur.fetchone()[0]
                if result:
                    return True, f"Query with PID {pid} was successfully terminated."
                else:
                    return False, f"Could not terminate query with PID {pid}. Check permissions or if the query is already finished."
    except Exception as e:
        logger.error(f"Failed to terminate query {pid}: {str(e)}")
        return False, str(e)
