"""
Общее состояние между userbot.py и bot.py.
"""
from telethon import TelegramClient

userbot_clients: dict[int, TelegramClient] = {}
notifier: TelegramClient | None = None
aiogram_bot = None
