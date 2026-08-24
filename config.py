"""
Конфигурация приложения.
"""
import os
from types import SimpleNamespace

API_ID = int(os.getenv("TG_API_ID", "38904811"))
API_HASH = os.getenv("TG_API_HASH", "a0be67ff4bae5e1766d64d2da45ff7e1").strip()
SESSION_NAME = "sessions/userbot"
BOT_TOKEN = "8976406641:AAF5zTZAvHJV117zf3K9-pMMFmkupsn5vJA"
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
