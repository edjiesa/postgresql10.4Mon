import psutil
import os
import logging

logger = logging.getLogger("system_monitor")

def get_system_metrics():
    """
    Retrieves host-level system resource metrics: CPU, RAM, and Disk.
    """
    try:
        # CPU usage
        cpu_percent = psutil.cpu_percent(interval=None)
        
        # Virtual Memory
        mem = psutil.virtual_memory()
        ram_total = mem.total
        ram_used = mem.used
        ram_percent = mem.percent
        
        # Disk usage of the current workspace drive/partition
        drive = os.path.splitdrive(os.getcwd())[0] or 'C:'
        disk = psutil.disk_usage(drive)
        disk_total = disk.total
        disk_used = disk.used
        disk_percent = disk.percent
        
        return {
            "status": "success",
            "cpu": {
                "percent": cpu_percent
            },
            "ram": {
                "total": ram_total,
                "used": ram_used,
                "percent": ram_percent
            },
            "disk": {
                "drive": drive,
                "total": disk_total,
                "used": disk_used,
                "percent": disk_percent
            }
        }
    except Exception as e:
        logger.error(f"Error fetching system metrics: {e}")
        return {
            "status": "error",
            "message": str(e)
        }
