import asyncio
import os
import time
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend import config, db_monitor, alerts

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("main")

# Global metrics cache: db_id -> metrics dict
DB_METRICS_CACHE = {}

async def check_and_alert_db(db):
    """
    Checks database health and triggers alerts if metrics violate thresholds.
    """
    db_id = db['id']
    db_name = db['name']
    
    try:
        # Fetch metrics in thread pool to prevent blocking the async loop
        metrics = await asyncio.to_thread(db_monitor.check_db_metrics, db)
        
        # Cache results
        DB_METRICS_CACHE[db_id] = metrics
        
        # Analyze and alert
        active_conn = metrics["active_connections"]
        max_conn = metrics["max_connections"]
        usage_pct = int(active_conn / max_conn * 100) if max_conn > 0 else 0
        conn_threshold = db.get("max_conn_threshold", 80)
        
        # 1. Active Connections Alert
        if usage_pct >= conn_threshold:
            severity = "critical" if usage_pct >= 95 else "warning"
            alerts.trigger_alert(
                db_id=db_id,
                db_name=db_name,
                alert_type="connection_limit",
                severity=severity,
                message=f"Database connections usage is high: {active_conn}/{max_conn} ({usage_pct}%). Threshold: {conn_threshold}%.",
                details={"active_connections": active_conn, "max_connections": max_conn, "usage_percent": usage_pct},
                item_key=f"conn_{usage_pct}"
            )
            
        # 2. Slow Queries Alert
        for q in metrics["slow_queries"]:
            pid = q["pid"]
            duration = q["duration_seconds"]
            alerts.trigger_alert(
                db_id=db_id,
                db_name=db_name,
                alert_type="slow_query",
                severity="warning",
                message=f"Slow query detected running for {duration} seconds (PID: {pid}).",
                details=q,
                item_key=f"slow_{pid}"
            )
            
        # 3. Blocking Lock Alerts
        for lock in metrics["blocking_queries"]:
            blocked_pid = lock["blocked_pid"]
            blocking_pid = lock["blocking_pid"]
            alerts.trigger_alert(
                db_id=db_id,
                db_name=db_name,
                alert_type="blocking_lock",
                severity="critical",
                message=f"Query PID {blocked_pid} is blocked by PID {blocking_pid}.",
                details=lock,
                item_key=f"lock_{blocked_pid}_{blocking_pid}"
            )
            
    except Exception as e:
        logger.error(f"Failed to query metrics for database '{db_name}' (ID: {db_id}): {e}")
        # Cache failure state
        DB_METRICS_CACHE[db_id] = {
            "status": "offline",
            "error": str(e),
            "timestamp": time.time(),
            "pg_version": "Offline",
            "db_size": "Offline",
            "active_connections": 0,
            "max_connections": 0,
            "cache_hit_ratio": 0.0,
            "index_hit_ratio": 0.0,
            "slow_queries": [],
            "blocking_queries": []
        }
        # Trigger Database Offline Alert
        alerts.trigger_alert(
            db_id=db_id,
            db_name=db_name,
            alert_type="offline",
            severity="critical",
            message=f"Could not connect to database: {str(e)}",
            item_key="offline"
        )

async def monitor_scheduler():
    """
    Background loop checking registered databases on their configured intervals.
    """
    last_checked = {}  # db_id -> timestamp
    
    while True:
        try:
            databases = config.get_databases()
            for db in databases:
                if not db["is_active"]:
                    continue
                
                db_id = db["id"]
                check_interval = db.get("check_interval", 30)
                now = time.time()
                
                # Check if database is due for polling
                if now - last_checked.get(db_id, 0) >= check_interval:
                    last_checked[db_id] = now
                    # Spawn checking task concurrently without waiting for it
                    asyncio.create_task(check_and_alert_db(db))
                    
        except Exception as e:
            logger.error(f"Error in monitor scheduler loop: {e}")
            
        await asyncio.sleep(5)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init database and launch monitor background task
    logger.info("Initializing SQLite tables...")
    config.init_db()
    
    # Run a quick startup pass to check active databases
    databases = config.get_databases()
    for db in databases:
        if db["is_active"]:
            asyncio.create_task(check_and_alert_db(db))
            
    # Start scheduler loop
    scheduler_task = asyncio.create_task(monitor_scheduler())
    yield
    # Shutdown
    scheduler_task.cancel()
    logger.info("Shutting down background scheduler...")

# Initialize FastAPI App
app = FastAPI(
    title="PostgreSQL Performance Monitor",
    lifespan=lifespan
)

# Enable CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Pydantic API Models ---

class DBConfigModel(BaseModel):
    name: str = Field(..., min_length=1)
    host: str = Field(..., min_length=1)
    port: int = Field(5432, ge=1, le=65535)
    username: str = Field(..., min_length=1)
    password: str
    dbname: str = Field(..., min_length=1)
    sslmode: str = Field("prefer")
    slow_query_threshold: int = Field(5, ge=1)
    max_conn_threshold: int = Field(80, ge=1, le=100)
    check_interval: int = Field(30, ge=5)
    is_active: int = Field(1, ge=0, le=1)

class AlertSettingsModel(BaseModel):
    config: dict
    is_enabled: bool

# --- REST API Endpoints ---

@app.get("/api/databases")
async def api_get_databases():
    try:
        dbs = config.get_databases()
        # Merge configuration with cached metrics
        for db in dbs:
            db_id = db["id"]
            if db_id in DB_METRICS_CACHE:
                db["metrics"] = DB_METRICS_CACHE[db_id]
            else:
                db["metrics"] = {
                    "status": "pending",
                    "pg_version": "Pending...",
                    "db_size": "Pending...",
                    "active_connections": 0,
                    "max_connections": 0,
                    "cache_hit_ratio": 0.0,
                    "index_hit_ratio": 0.0,
                    "slow_queries": [],
                    "blocking_queries": []
                }
        return dbs
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/databases/{db_id}")
async def api_get_database(db_id: int):
    db = config.get_database(db_id)
    if not db:
        raise HTTPException(status_code=404, detail="Database configuration not found.")
    
    if db_id in DB_METRICS_CACHE:
        db["metrics"] = DB_METRICS_CACHE[db_id]
    return db

@app.post("/api/databases")
async def api_add_database(db: DBConfigModel):
    # Test connection first to verify details
    db_dict = db.model_dump()
    success, message = await asyncio.to_thread(db_monitor.test_connection, db_dict)
    if not success:
        raise HTTPException(status_code=400, detail=f"Connection test failed: {message}")
        
    try:
        new_id = config.add_database(**db_dict)
        db_dict["id"] = new_id
        # Run async check immediately to populate metrics cache
        asyncio.create_task(check_and_alert_db(db_dict))
        return {"id": new_id, "message": "Database added successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/databases/{db_id}")
async def api_update_database(db_id: int, db: DBConfigModel):
    # Verify DB exists
    existing = config.get_database(db_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Database not found.")
        
    db_dict = db.model_dump()
    # Test connection
    success, message = await asyncio.to_thread(db_monitor.test_connection, db_dict)
    if not success:
        raise HTTPException(status_code=400, detail=f"Connection test failed: {message}")

    try:
        config.update_database(db_id, **db_dict)
        db_dict["id"] = db_id
        # Re-trigger check
        asyncio.create_task(check_and_alert_db(db_dict))
        return {"message": "Database updated successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/databases/{db_id}")
async def api_delete_database(db_id: int):
    try:
        success = config.delete_database(db_id)
        if not success:
            raise HTTPException(status_code=404, detail="Database not found.")
        # Remove from metrics cache
        if db_id in DB_METRICS_CACHE:
            del DB_METRICS_CACHE[db_id]
        return {"message": "Database deleted successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/databases/test")
async def api_test_database_connection(db: DBConfigModel):
    success, message = await asyncio.to_thread(db_monitor.test_connection, db.model_dump())
    return {"success": success, "message": message}

@app.post("/api/databases/{db_id}/kill/{pid}")
async def api_kill_query(db_id: int, pid: int):
    db = config.get_database(db_id)
    if not db:
        raise HTTPException(status_code=404, detail="Database not found.")
        
    success, message = await asyncio.to_thread(db_monitor.terminate_query, db, pid)
    if not success:
        raise HTTPException(status_code=500, detail=message)
        
    # Re-run metrics collection immediately to refresh UI
    asyncio.create_task(check_and_alert_db(db))
    return {"message": message}

@app.get("/api/alerts")
async def api_get_alerts():
    try:
        return config.get_all_alert_settings()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/alerts/{channel}")
async def api_update_alerts(channel: str, data: AlertSettingsModel):
    if channel not in ["telegram", "discord", "slack"]:
        raise HTTPException(status_code=400, detail="Invalid alert channel.")
        
    try:
        config.update_alert_settings(channel, data.config, data.is_enabled)
        return {"message": f"Alert settings for {channel} updated."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/logs")
async def api_get_logs():
    try:
        return config.get_alert_logs(limit=100)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/logs")
async def api_clear_logs():
    try:
        config.clear_alert_logs()
        return {"message": "Alert logs cleared."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- Serve Static Frontend Files ---

# Create frontend folder if it doesn't exist
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")
os.makedirs(frontend_dir, exist_ok=True)

# Mount files served static
app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

@app.get("/")
async def serve_index():
    index_path = os.path.join(frontend_dir, "index.html")
    if not os.path.exists(index_path):
        return {"message": "PostgreSQL Monitor API is running. Frontend index.html not yet created."}
    return FileResponse(index_path)

if __name__ == "__main__":
    import uvicorn
    # Start web server on port 8000
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
