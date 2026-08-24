"""
Конфигурация приложения.
"""
import os
from types import SimpleNamespace

API_ID = int(os.getenv("TG_API_ID", "38904811"))
API_HASH = os.getenv("TG_API_HASH", "").strip()
SESSION_NAME = "sessions/userbot"
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = [5949150362]
SEND_DELAY_MIN_SECONDS = 2.0
SEND_DELAY_MAX_SECONDS = 4.5
DB_PATH = "broadcast.db"
TIMEZONE = "Europe/Berlin"

cfg = SimpleNamespace(
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    admins=ADMIN_IDS,
)
