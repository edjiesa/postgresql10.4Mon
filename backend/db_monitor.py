import psycopg
from psycopg.rows import dict_row
import time
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("db_monitor")

def get_connection_params(db_config):
    """
    Construct connection parameters dictionary from config.
    """
    return {
        "host": db_config.get('host'),
        "port": int(db_config.get('port', 5432)),
        "user": db_config.get('username'),
        "password": db_config.get('password'),
        "dbname": db_config.get('dbname'),
        "sslmode": db_config.get('sslmode', 'prefer'),
        "connect_timeout": 5
    }

def test_connection(db_config):
    """
    Test connection to a remote PostgreSQL server.
    Returns (success_boolean, message)
    """
    conn_params = get_connection_params(db_config)
    try:
        with psycopg.connect(**conn_params) as conn:
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
    conn_params = get_connection_params(db_config)
    metrics = {
        "status": "online",
        "pg_version": "Unknown",
        "db_size": "Unknown",
        "active_connections": 0,
        "max_connections": 100,
        "cache_hit_ratio": 0.0,
        "index_hit_ratio": 0.0,
        "slow_queries": [],
        "active_queries": [],
        "idle_queries": [],
        "blocking_queries": [],
        "temp_files": 0,
        "temp_bytes": 0,
        "autovacuum_workers": [],
        "dead_tuples_tables": [],
        "wraparound_stats": {
            "db_wraparound": [],
            "table_wraparound": []
        },
        "replication_stats": {
            "is_replica": False,
            "replica_lag_seconds": 0.0,
            "standby_clients": []
        },
        "timestamp": time.time()
    }
    
    slow_threshold = db_config.get('slow_query_threshold', 5)
    
    with psycopg.connect(**conn_params, row_factory=dict_row, autocommit=True) as conn:
        with conn.cursor() as cur:
            # Detect numeric PostgreSQL version (e.g. 90113 for 9.1.13, 100004 for 10.4)
            pg_version_num = 100000
            try:
                cur.execute("SELECT current_setting('server_version_num')::int;")
                v_row = cur.fetchone()
                if v_row:
                    pg_version_num = list(v_row.values())[0] or 100000
            except Exception:
                pg_version_num = 100000

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

            # 1b. Temp files and bytes (temp_files added in PG 9.2)
            try:
                if pg_version_num >= 90200:
                    cur.execute("""
                        SELECT 
                            COALESCE(sum(temp_files), 0) AS temp_files,
                            COALESCE(sum(temp_bytes), 0) AS temp_bytes
                        FROM pg_stat_database
                        WHERE datname = current_database();
                    """)
                    row = cur.fetchone()
                    if row:
                        metrics["temp_files"] = int(row.get("temp_files", 0))
                        metrics["temp_bytes"] = int(row.get("temp_bytes", 0))
            except Exception as e:
                logger.warning(f"Error querying database temp files: {e}")

            # 2. Cache Hit Ratio
            try:
                cur.execute("""
                    SELECT 
                        CASE 
                            WHEN COALESCE(sum(blks_hit), 0) + COALESCE(sum(blks_read), 0) = 0 THEN 0.0
                            ELSE round(((COALESCE(sum(blks_hit), 0)::numeric / (COALESCE(sum(blks_hit), 0) + COALESCE(sum(blks_read), 0))::numeric) * 100.0), 2)
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
                            WHEN COALESCE(sum(idx_blks_hit), 0) + COALESCE(sum(idx_blks_read), 0) = 0 THEN 0.0
                            ELSE round(((COALESCE(sum(idx_blks_hit), 0)::numeric / (COALESCE(sum(idx_blks_hit), 0) + COALESCE(sum(idx_blks_read), 0))::numeric) * 100.0), 2)
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
                if pg_version_num < 90200:
                    cur.execute("""
                        SELECT 
                            procpid AS pid,
                            usename AS username,
                            client_addr::text AS client_ip,
                            backend_start,
                            query_start,
                            'active' AS state,
                            '' AS wait_event_type,
                            '' AS wait_event,
                            current_query AS query,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS duration_seconds
                        FROM pg_stat_activity
                        WHERE current_query NOT LIKE '<IDLE>%%'
                          AND current_query != '<IDLE>'
                          AND procpid != pg_backend_pid()
                          AND (current_query IS NULL OR current_query NOT LIKE '%%pg_stat_activity%%')
                          AND query_start IS NOT NULL
                          AND (clock_timestamp() - query_start) > (%s * interval '1 second')
                        ORDER BY duration_seconds DESC;
                    """, (slow_threshold,))
                elif pg_version_num < 90600:
                    cur.execute("""
                        SELECT 
                            pid,
                            usename AS username,
                            client_addr::text AS client_ip,
                            backend_start,
                            query_start,
                            state,
                            '' AS wait_event_type,
                            '' AS wait_event,
                            query,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS duration_seconds
                        FROM pg_stat_activity
                        WHERE state != 'idle'
                          AND pid != pg_backend_pid()
                          AND (query IS NULL OR query NOT LIKE '%%pg_stat_activity%%')
                          AND query_start IS NOT NULL
                          AND (clock_timestamp() - query_start) > (%s * interval '1 second')
                        ORDER BY duration_seconds DESC;
                    """, (slow_threshold,))
                else:
                    cur.execute("""
                        SELECT 
                            pid,
                            usename AS username,
                            client_addr::text AS client_ip,
                            backend_start,
                            query_start,
                            state,
                            wait_event_type,
                            wait_event,
                            query,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS duration_seconds
                        FROM pg_stat_activity
                        WHERE state != 'idle'
                          AND pid != pg_backend_pid()
                          AND (query IS NULL OR query NOT LIKE '%%pg_stat_activity%%')
                          AND query_start IS NOT NULL
                          AND (clock_timestamp() - query_start) > (%s * interval '1 second')
                        ORDER BY duration_seconds DESC;
                    """, (slow_threshold,))
                rows = cur.fetchall()
                slow_list = []
                for r in rows:
                    d = dict(r)
                    d["duration_seconds"] = float(d.get("duration_seconds") or 0.0)
                    slow_list.append(d)
                metrics["slow_queries"] = slow_list
            except Exception as e:
                logger.warning(f"Error querying slow queries: {e}")

            # 4b. All Active and Idle Sessions
            try:
                if pg_version_num < 90200:
                    cur.execute("""
                        SELECT 
                            procpid AS pid,
                            usename AS username,
                            client_addr::text AS client_ip,
                            backend_start,
                            query_start,
                            query_start AS state_change,
                            CASE 
                                WHEN current_query = '<IDLE>' THEN 'idle'
                                WHEN current_query LIKE '<IDLE>%%' THEN substring(current_query from 2 for position('>' in current_query)-2)
                                ELSE 'active'
                            END AS state,
                            '' AS wait_event_type,
                            '' AS wait_event,
                            current_query AS query,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS query_duration_seconds,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS idle_duration_seconds
                        FROM pg_stat_activity
                        WHERE procpid != pg_backend_pid()
                          AND (current_query IS NULL OR current_query NOT LIKE '%%pg_stat_activity%%')
                        ORDER BY query_start DESC;
                    """)
                elif pg_version_num < 90600:
                    cur.execute("""
                        SELECT 
                            pid,
                            usename AS username,
                            client_addr::text AS client_ip,
                            backend_start,
                            query_start,
                            state_change,
                            state,
                            '' AS wait_event_type,
                            '' AS wait_event,
                            query,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS query_duration_seconds,
                            COALESCE(round(extract(epoch from (clock_timestamp() - state_change))::numeric, 2), 0.0) AS idle_duration_seconds
                        FROM pg_stat_activity
                        WHERE pid != pg_backend_pid()
                          AND (query IS NULL OR query NOT LIKE '%%pg_stat_activity%%')
                        ORDER BY COALESCE(query_start, state_change) DESC;
                    """)
                else:
                    cur.execute("""
                        SELECT 
                            pid,
                            usename AS username,
                            client_addr::text AS client_ip,
                            backend_start,
                            query_start,
                            state_change,
                            state,
                            wait_event_type,
                            wait_event,
                            query,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS query_duration_seconds,
                            COALESCE(round(extract(epoch from (clock_timestamp() - state_change))::numeric, 2), 0.0) AS idle_duration_seconds
                        FROM pg_stat_activity
                        WHERE pid != pg_backend_pid()
                          AND (query IS NULL OR query NOT LIKE '%%pg_stat_activity%%')
                        ORDER BY COALESCE(query_start, state_change) DESC;
                    """)
                rows = cur.fetchall()
                active_list = []
                idle_list = []
                for r in rows:
                    d = dict(r)
                    state = d.get("state")
                    if state == "active":
                        d["duration_seconds"] = float(d.get("query_duration_seconds") or 0.0)
                        active_list.append(d)
                    elif state == "idle":
                        continue
                    else:
                        d["duration_seconds"] = float(d.get("idle_duration_seconds") or 0.0)
                        idle_list.append(d)
                active_list.sort(key=lambda x: x.get("duration_seconds", 0.0), reverse=True)
                idle_list.sort(key=lambda x: x.get("duration_seconds", 0.0), reverse=True)
                metrics["active_queries"] = active_list
                metrics["idle_queries"] = idle_list
            except Exception as e:
                logger.warning(f"Error querying sessions: {e}")

            # 5. Blocking Locks
            try:
                if pg_version_num < 90200:
                    cur.execute("""
                        SELECT
                            blocked_locks.procpid     AS blocked_pid,
                            blocked_activity.usename  AS blocked_user,
                            blocked_activity.current_query    AS blocked_statement,
                            blocking_locks.procpid    AS blocking_pid,
                            blocking_activity.usename AS blocking_user,
                            blocking_activity.current_query   AS blocking_statement,
                            COALESCE(round(extract(epoch from (clock_timestamp() - blocked_activity.query_start))::numeric, 2), 0.0) AS blocked_duration_seconds
                        FROM pg_catalog.pg_locks         blocked_locks
                        JOIN pg_catalog.pg_stat_activity blocked_activity ON blocked_activity.procpid = blocked_locks.procpid
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
                            AND blocking_locks.procpid != blocked_locks.procpid
                        JOIN pg_catalog.pg_stat_activity blocking_activity ON blocking_activity.procpid = blocking_locks.procpid
                        WHERE NOT blocked_locks.granted;
                    """)
                else:
                    cur.execute("""
                        SELECT
                            blocked_locks.pid     AS blocked_pid,
                            blocked_activity.usename  AS blocked_user,
                            blocked_activity.query    AS blocked_statement,
                            blocking_locks.pid    AS blocking_pid,
                            blocking_activity.usename AS blocking_user,
                            blocking_activity.query   AS blocking_statement,
                            COALESCE(round(extract(epoch from (clock_timestamp() - blocked_activity.query_start))::numeric, 2), 0.0) AS blocked_duration_seconds
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
                lock_list = []
                for r in rows:
                    d = dict(r)
                    d["blocked_duration_seconds"] = float(d.get("blocked_duration_seconds") or 0.0)
                    lock_list.append(d)
                metrics["blocking_queries"] = lock_list
            except Exception as e:
                logger.warning(f"Error querying blocking locks: {e}")

            # 6. Autovacuum Workers
            try:
                if pg_version_num < 90200:
                    cur.execute("""
                        SELECT 
                            procpid AS pid,
                            current_query AS query,
                            'active' AS state,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS duration_seconds
                        FROM pg_stat_activity
                        WHERE current_query LIKE 'autovacuum%%' 
                          AND procpid != pg_backend_pid();
                    """)
                else:
                    cur.execute("""
                        SELECT 
                            pid,
                            query,
                            state,
                            COALESCE(round(extract(epoch from (clock_timestamp() - query_start))::numeric, 2), 0.0) AS duration_seconds
                        FROM pg_stat_activity
                        WHERE query LIKE 'autovacuum%%' 
                          AND pid != pg_backend_pid();
                    """)
                rows = cur.fetchall()
                vacuum_list = []
                for r in rows:
                    d = dict(r)
                    d["duration_seconds"] = float(d.get("duration_seconds") or 0.0)
                    vacuum_list.append(d)
                metrics["autovacuum_workers"] = vacuum_list
            except Exception as e:
                logger.warning(f"Error querying autovacuum workers: {e}")

            # 7. Dead Tuples Tables
            try:
                cur.execute("""
                    SELECT 
                        schemaname || '.' || relname AS table_name,
                        n_dead_tup AS dead_tuples,
                        n_live_tup AS live_tuples,
                        round(((n_dead_tup::numeric / NULLIF(n_dead_tup + n_live_tup, 0)::numeric) * 100.0), 2) AS dead_tuples_ratio,
                        last_vacuum,
                        last_autovacuum,
                        last_analyze,
                        last_autoanalyze
                    FROM pg_stat_user_tables
                    ORDER BY n_dead_tup DESC
                    LIMIT 5;
                """)
                rows = cur.fetchall()
                res_rows = []
                for r in rows:
                    d = dict(r)
                    for k in ["last_vacuum", "last_autovacuum", "last_analyze", "last_autoanalyze"]:
                        if d.get(k):
                            d[k] = d[k].isoformat() if hasattr(d[k], "isoformat") else str(d[k])
                    res_rows.append(d)
                metrics["dead_tuples_tables"] = res_rows
            except Exception as e:
                logger.warning(f"Error querying dead tuples: {e}")

            # 8. Transaction ID Wraparound (Databases & Tables age)
            try:
                cur.execute("""
                    SELECT 
                        datname,
                        age(datfrozenxid) AS txid_age,
                        2147483648 - age(datfrozenxid) AS txids_remaining,
                        round(((age(datfrozenxid)::numeric / 2147483648::numeric) * 100.0), 2) AS wraparound_percent
                    FROM pg_database
                    WHERE datallowconn
                    ORDER BY txid_age DESC;
                """)
                db_rows = cur.fetchall()
                metrics["wraparound_stats"]["db_wraparound"] = [dict(r) for r in db_rows]

                cur.execute("""
                    SELECT 
                        c.oid::regclass::text AS table_name,
                        age(c.relfrozenxid) AS table_age,
                        round(((age(c.relfrozenxid)::numeric / 2147483648::numeric) * 100.0), 2) AS table_wraparound_percent
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE c.relkind = 'r'
                      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY table_age DESC
                    LIMIT 5;
                """)
                table_rows = cur.fetchall()
                metrics["wraparound_stats"]["table_wraparound"] = [dict(r) for r in table_rows]
            except Exception as e:
                logger.warning(f"Error querying txid wraparound: {e}")

            # 9. Replication Lag
            try:
                cur.execute("SELECT pg_is_in_recovery();")
                is_recovery = cur.fetchone()
                
                if is_recovery and is_recovery.get("pg_is_in_recovery"):
                    metrics["replication_stats"]["is_replica"] = True
                    if pg_version_num < 100000:
                        cur.execute("""
                            SELECT 
                                pg_last_xlog_receive_location()::text AS last_receive_lsn,
                                pg_last_xlog_replay_location()::text AS last_replay_lsn,
                                pg_last_xact_replay_timestamp() AS last_replay_timestamp,
                                COALESCE(round(extract(epoch from (now() - pg_last_xact_replay_timestamp()))::numeric, 2), 0.0) AS replication_lag_seconds;
                        """)
                    else:
                        cur.execute("""
                            SELECT 
                                pg_last_wal_receive_lsn()::text AS last_receive_lsn,
                                pg_last_wal_replay_lsn()::text AS last_replay_lsn,
                                pg_last_xact_replay_timestamp() AS last_replay_timestamp,
                                COALESCE(round(extract(epoch from (now() - pg_last_xact_replay_timestamp()))::numeric, 2), 0.0) AS replication_lag_seconds;
                        """)
                    rep_row = cur.fetchone()
                    if rep_row:
                        metrics["replication_stats"]["replica_lag_seconds"] = float(rep_row.get("replication_lag_seconds") or 0.0)
                        if rep_row.get("last_replay_timestamp"):
                            metrics["replication_stats"]["last_replay_timestamp"] = rep_row.get("last_replay_timestamp").isoformat() if hasattr(rep_row.get("last_replay_timestamp"), "isoformat") else str(rep_row.get("last_replay_timestamp"))
                else:
                    metrics["replication_stats"]["is_replica"] = False
                    if pg_version_num < 100000:
                        cur.execute("""
                            SELECT 
                                client_addr::text AS standby_ip,
                                application_name,
                                state,
                                sync_state,
                                COALESCE(round((pg_xlog_location_diff(pg_current_xlog_location(), replay_location) / 1024 / 1024)::numeric, 2), 0.0) AS lag_mb
                            FROM pg_stat_replication;
                        """)
                    else:
                        cur.execute("""
                            SELECT 
                                client_addr::text AS standby_ip,
                                application_name,
                                state,
                                sync_state,
                                COALESCE(round((pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn) / 1024 / 1024)::numeric, 2), 0.0) AS lag_mb
                            FROM pg_stat_replication;
                        """)
                    standby_rows = cur.fetchall()
                    standby_list = []
                    for r in standby_rows:
                        d = dict(r)
                        d["lag_mb"] = float(d.get("lag_mb") or 0.0)
                        standby_list.append(d)
                    metrics["replication_stats"]["standby_clients"] = standby_list
            except Exception as e:
                logger.warning(f"Error querying replication lag: {e}")

    return metrics

def terminate_query(db_config, pid):
    """
    Terminates a specific query execution by pid.
    Returns (success_boolean, message)
    """
    conn_params = get_connection_params(db_config)
    try:
        with psycopg.connect(**conn_params) as conn:
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
