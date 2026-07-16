import requests
import json
import time
import logging
from backend.config import get_all_alert_settings, add_alert_log

logger = logging.getLogger("alerts")

# In-memory store for alert throttling
# Key: (db_id, alert_type, item_key) -> Value: timestamp of last sent alert
_alert_throttle_cache = {}
THROTTLE_SECONDS = 600  # Default throttle: 10 minutes

def should_throttle(db_id, alert_type, item_key=""):
    """
    Checks if an alert should be throttled to prevent spam.
    """
    now = time.time()
    key = (db_id, alert_type, item_key)
    last_sent = _alert_throttle_cache.get(key, 0)
    
    if now - last_sent < THROTTLE_SECONDS:
        return True
    
    # Update last sent timestamp
    _alert_throttle_cache[key] = now
    return False

def send_telegram_message(bot_token, chat_id, message):
    """
    Sends a message via Telegram Bot API.
    """
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            logger.error(f"Telegram alert failed: {response.text}")
            return False, response.text
        return True, "Success"
    except Exception as e:
        logger.error(f"Telegram request exception: {e}")
        return False, str(e)

def send_webhook_message(webhook_url, payload):
    """
    Sends a message to Discord or Slack webhook.
    """
    try:
        response = requests.post(
            webhook_url, 
            data=json.dumps(payload), 
            headers={"Content-Type": "application/json"},
            timeout=10
        )
        if response.status_code not in [200, 204]:
            logger.error(f"Webhook alert failed: {response.status_code} - {response.text}")
            return False, response.text
        return True, "Success"
    except Exception as e:
        logger.error(f"Webhook request exception: {e}")
        return False, str(e)

def trigger_alert(db_id, db_name, alert_type, severity, message, details=None, item_key=""):
    """
    Triggers an alert. Saves to local SQLite database and pushes to configured external channels.
    """
    # 1. Save alert log in local SQLite DB
    try:
        add_alert_log(
            database_id=db_id,
            database_name=db_name,
            alert_type=alert_type,
            severity=severity,
            message=message,
            details_dict=details
        )
    except Exception as e:
        logger.error(f"Failed to save alert log to SQLite: {e}")

    # 2. Check throttling
    if should_throttle(db_id, alert_type, item_key):
        logger.info(f"Alert throttled: DB={db_name}, Type={alert_type}, ItemKey={item_key}")
        return

    # 3. Retrieve alert settings
    try:
        settings = get_all_alert_settings()
    except Exception as e:
        logger.error(f"Failed to fetch alert settings: {e}")
        return

    # Formatted messages
    emoji_map = {
        "info": "ℹ️",
        "warning": "⚠️",
        "critical": "🚨"
    }
    emoji = emoji_map.get(severity.lower(), "📢")
    
    # Text-based format (Telegram / Slack)
    text_message = (
        f"{emoji} <b>[POSTGRESQL MON]</b>\n"
        f"<b>Database:</b> {db_name}\n"
        f"<b>Alert:</b> {alert_type.replace('_', ' ').title()}\n"
        f"<b>Severity:</b> {severity.upper()}\n"
        f"<b>Time:</b> {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())}\n\n"
        f"<b>Message:</b> {message}"
    )
    
    if details:
        if alert_type == "slow_query":
            text_message += (
                f"\n\n<b>Details:</b>\n"
                f"• PID: {details.get('pid')}\n"
                f"• User: {details.get('username')}\n"
                f"• Client IP: {details.get('client_ip') or 'local'}\n"
                f"• Duration: {details.get('duration_seconds')} seconds\n"
                f"• Query: <code>{details.get('query', '')[:300]}...</code>"
            )
        elif alert_type == "blocking_lock":
            text_message += (
                f"\n\n<b>Details:</b>\n"
                f"• Blocked PID: {details.get('blocked_pid')}\n"
                f"• Blocked Query: <code>{details.get('blocked_statement', '')[:150]}...</code>\n"
                f"• Blocking PID: {details.get('blocking_pid')}\n"
                f"• Blocking User: {details.get('blocking_user')}\n"
                f"• Blocking Query: <code>{details.get('blocking_statement', '')[:150]}...</code>\n"
                f"• Duration: {details.get('blocked_duration_seconds')} seconds"
            )
        elif alert_type == "connection_limit":
            text_message += (
                f"\n\n<b>Details:</b>\n"
                f"• Connections Active: {details.get('active_connections')}\n"
                f"• Connections Max: {details.get('max_connections')}\n"
                f"• Usage: {details.get('usage_percent')}%"
            )
        elif alert_type == "replication_lag":
            if details.get("standby_ip"):
                text_message += (
                    f"\n\n<b>Details:</b>\n"
                    f"• Standby IP: {details.get('standby_ip')}\n"
                    f"• App Name: {details.get('application_name')}\n"
                    f"• State: {details.get('state')}\n"
                    f"• Sync State: {details.get('sync_state')}\n"
                    f"• Lag: {details.get('lag_mb')} MB"
                )
            else:
                text_message += (
                    f"\n\n<b>Details:</b>\n"
                    f"• Delay: {details.get('replica_lag_seconds')} seconds\n"
                    f"• Last Replay: {details.get('last_replay_timestamp')}"
                )

    # Dispatch alerts
    # A. Telegram
    tg = settings.get("telegram")
    if tg and tg.get("is_enabled"):
        cfg = tg.get("config", {})
        bot_token = cfg.get("bot_token")
        chat_id = cfg.get("chat_id")
        if bot_token and chat_id:
            send_telegram_message(bot_token, chat_id, text_message)

    # B. Discord
    discord = settings.get("discord")
    if discord and discord.get("is_enabled"):
        cfg = discord.get("config", {})
        webhook_url = cfg.get("webhook_url")
        if webhook_url:
            discord_payload = {
                "username": "PostgreSQL Monitor",
                "embeds": [{
                    "title": f"{emoji} DB Performance Alert: {db_name}",
                    "color": 15158332 if severity == "critical" else (15844367 if severity == "warning" else 3447003),
                    "description": message,
                    "fields": [
                        {"name": "Alert Type", "value": alert_type.replace('_', ' ').title(), "inline": True},
                        {"name": "Severity", "value": severity.upper(), "inline": True},
                        {"name": "Timestamp", "value": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()), "inline": True}
                    ],
                    "footer": {"text": "PostgreSQL Monitoring System"}
                }]
            }
            # Append details if present
            if details:
                detail_text = ""
                for k, v in details.items():
                    if k == 'query' or k == 'blocked_statement' or k == 'blocking_statement':
                        v = str(v)[:200] + "..." if len(str(v)) > 200 else str(v)
                    detail_text += f"**{k.replace('_', ' ').title()}**: {v}\n"
                discord_payload["embeds"][0]["fields"].append({
                    "name": "Details",
                    "value": detail_text or "No detailed attributes"
                })
            send_webhook_message(webhook_url, discord_payload)

    # C. Slack
    slack = settings.get("slack")
    if slack and slack.get("is_enabled"):
        cfg = slack.get("config", {})
        webhook_url = cfg.get("webhook_url")
        if webhook_url:
            # Simple slack notification markdown
            mrkdwn_text = text_message.replace("<b>", "*").replace("</b>", "*").replace("<code>", "`").replace("</code>", "`")
            slack_payload = {
                "text": mrkdwn_text
            }
            send_webhook_message(webhook_url, slack_payload)

    # D. n8n Webhook
    n8n = settings.get("n8n")
    if n8n and n8n.get("is_enabled"):
        cfg = n8n.get("config", {})
        webhook_url = cfg.get("webhook_url")
        if webhook_url:
            n8n_payload = {
                "event": "database_performance_alert",
                "database_id": db_id,
                "database_name": db_name,
                "alert_type": alert_type,
                "severity": severity,
                "message": message,
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
                "details": details
            }
            send_webhook_message(webhook_url, n8n_payload)

    # E. Google Chat
    gchat = settings.get("google_chat")
    if gchat and gchat.get("is_enabled"):
        cfg = gchat.get("config", {})
        webhook_url = cfg.get("webhook_url")
        if webhook_url:
            mrkdwn_text = text_message.replace("<b>", "*").replace("</b>", "*").replace("<code>", "`").replace("</code>", "`")
            gchat_payload = {
                "text": mrkdwn_text
            }
            send_webhook_message(webhook_url, gchat_payload)

def send_test_alert(channel_name, config_dict):
    """
    Sends a mock test alert to the specified channel to verify credentials.
    Returns (success_bool, message_str).
    """
    emoji = "🧪"
    db_name = "Test_Database"
    alert_type = "connection_limit"
    severity = "warning"
    message = "This is a mock performance alert to test your PG-Mon channel configuration."
    timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
    
    text_message = (
        f"{emoji} <b>[POSTGRESQL MON - TEST]</b>\n"
        f"<b>Database:</b> {db_name}\n"
        f"<b>Alert:</b> Connection Limit Warning\n"
        f"<b>Severity:</b> WARNING\n"
        f"<b>Time:</b> {timestamp}\n\n"
        f"<b>Message:</b> {message}"
    )

    if channel_name == "telegram":
        bot_token = config_dict.get("bot_token")
        chat_id = config_dict.get("chat_id")
        if not bot_token or not chat_id:
            return False, "Bot token and Chat ID are required."
        return send_telegram_message(bot_token, chat_id, text_message)
        
    elif channel_name == "discord":
        webhook_url = config_dict.get("webhook_url")
        if not webhook_url:
            return False, "Webhook URL is required."
        discord_payload = {
            "username": "PostgreSQL Monitor Test",
            "embeds": [{
                "title": f"{emoji} DB Performance Alert (TEST): {db_name}",
                "color": 15844367, # warning yellow
                "description": message,
                "fields": [
                    {"name": "Alert Type", "value": "Connection Limit Warning", "inline": True},
                    {"name": "Severity", "value": "WARNING", "inline": True},
                    {"name": "Timestamp", "value": timestamp, "inline": True}
                ],
                "footer": {"text": "PostgreSQL Monitoring Test System"}
            }]
        }
        return send_webhook_message(webhook_url, discord_payload)
        
    elif channel_name == "slack":
        webhook_url = config_dict.get("webhook_url")
        if not webhook_url:
            return False, "Webhook URL is required."
        mrkdwn_text = text_message.replace("<b>", "*").replace("</b>", "*").replace("<code>", "`").replace("</code>", "`")
        slack_payload = {
            "text": mrkdwn_text
        }
        return send_webhook_message(webhook_url, slack_payload)
        
    elif channel_name == "n8n":
        webhook_url = config_dict.get("webhook_url")
        if not webhook_url:
            return False, "Webhook URL is required."
        n8n_payload = {
            "event": "database_performance_alert_test",
            "database_id": 0,
            "database_name": db_name,
            "alert_type": alert_type,
            "severity": severity,
            "message": message,
            "timestamp": timestamp,
            "details": {
                "active_connections": 80,
                "max_connections": 100,
                "usage_percent": 80.0
            }
        }
        return send_webhook_message(webhook_url, n8n_payload)
        
    elif channel_name == "google_chat":
        webhook_url = config_dict.get("webhook_url")
        if not webhook_url:
            return False, "Webhook URL is required."
        mrkdwn_text = text_message.replace("<b>", "*").replace("</b>", "*").replace("<code>", "`").replace("</code>", "`")
        gchat_payload = {
            "text": mrkdwn_text
        }
        return send_webhook_message(webhook_url, gchat_payload)
        
    return False, f"Unknown alert channel: {channel_name}"

