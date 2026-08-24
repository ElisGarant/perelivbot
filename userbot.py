"""
Telegram Userbot — парсит сообщения по ключевым словам.
"""
import asyncio
import datetime
import json
import logging
import os
from pathlib import Path

from telethon import TelegramClient, events
from telethon.tl.types import Message

from config import cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [USERBOT] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)


def data_file_for(user_id: int) -> str:
    return f"data_{user_id}.json"


def session_name_for(user_id: int) -> str:
    return f"session_{user_id}"


def load_data(user_id: int) -> dict:
    try:
        with open(data_file_for(user_id), "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"chats": [], "keywords": [], "active": True, "dedup": True}


class _DataCache:
    def __init__(self, user_id: int):
        self._user_id = user_id
        self._path = data_file_for(user_id)
        self._mtime = -1.0
        self._data = {"chats": [], "keywords": [], "active": True, "dedup": True}

    def get(self) -> dict:
        try:
            mtime = os.path.getmtime(self._path)
        except OSError:
            return self._data
        if mtime != self._mtime:
            self._data = load_data(self._user_id)
            self._mtime = mtime
        return self._data

    def invalidate(self):
        self._mtime = -1.0


_caches = {}


def get_cache(user_id: int) -> _DataCache:
    if user_id not in _caches:
        _caches[user_id] = _DataCache(user_id)
    return _caches[user_id]


def matches_keywords(text: str, keywords: list[str]) -> list[str]:
    if not text:
        return []
    lower = text.lower()
    return [kw for kw in keywords if kw.lower() in lower]


def build_message_link(chat_username: str | None, chat_id: int, msg_id: int) -> str:
    if chat_username:
        return f"https://t.me/{chat_username}/{msg_id}"
    cid = str(abs(chat_id))
    if cid.startswith("100"):
        cid = cid[3:]
    return f"https://t.me/c/{cid}/{msg_id}"


def get_message_topic_id(msg: Message) -> int | None:
    reply_to = getattr(msg, "reply_to", None)
    if reply_to is None:
        return None
    top = getattr(reply_to, "reply_to_top_id", None)
    if top:
        return top
    if getattr(reply_to, "forum_topic", False):
        return getattr(reply_to, "reply_to_msg_id", None)
    return None


def bare_id(v) -> int:
    s = str(abs(int(v)))
    if s.startswith("100") and len(s) > 10:
        return int(s[3:])
    return int(s)


def find_matching_entries(chat_id: int, chat_username: str | None, topic_id: int | None, entries: list) -> list:
    matched = []
    for entry in entries:
        if isinstance(entry, dict):
            entry_chat_id = entry.get("id", "")
            entry_topic_id = entry.get("topic_id")
        else:
            entry_chat_id = str(entry)
            entry_topic_id = None
        chat_match = False
        try:
            if bare_id(entry_chat_id) == bare_id(chat_id):
                chat_match = True
        except (ValueError, TypeError):
            pass
        if not chat_match and isinstance(entry_chat_id, str) and chat_username:
            if entry_chat_id.lstrip("@").lower() == chat_username.lower():
                chat_match = True
        if not chat_match:
            continue
        if entry_topic_id is None:
            matched.append(entry)
        elif topic_id is not None and int(entry_topic_id) == int(topic_id):
            matched.append(entry)
    return matched


def format_entry_label(entry) -> str:
    if isinstance(entry, dict):
        title = entry.get("title", str(entry.get("id", "?")))
        topic_name = entry.get("topic_name")
        topic_id = entry.get("topic_id")
        if topic_id is not None:
            return f"{title} › {topic_name or f'топик #{topic_id}'}"
        return title
    return str(entry)


async def start_userbot_for(user_id: int, notifier: TelegramClient = None):
    import shared as _shared
    session = session_name_for(user_id)
    if not Path(f"{session}.session").exists():
        log.warning("Сессия для user_id=%s не найдена, пропускаем", user_id)
        return None
    client = TelegramClient(session, cfg.api_id, cfg.api_hash)
    try:
        await client.start()
    except Exception as exc:
        log.error("Не удалось запустить юзербот для user_id=%s: %s", user_id, exc)
        return None
    me = await client.get_me()
    log.info("Юзербот user_id=%s авторизован как @%s", user_id, me.username)
    _shared.userbot_clients[user_id] = client
    _start_time = datetime.datetime.now(datetime.timezone.utc)
    _seen = {}
    _DEDUP_TTL = datetime.timedelta(hours=1)
    _cache = get_cache(user_id)

    @client.on(events.NewMessage())
    async def handler(event: events.NewMessage.Event):
        data = _cache.get()
        entries = data.get("chats", [])
        keywords = data.get("keywords", [])
        if not data.get("active", True):
            return
        if not entries or not keywords:
            return
        chat = event.chat
        chat_id = event.chat_id
        username = getattr(chat, "username", None)
        msg: Message = event.message
        topic_id = get_message_topic_id(msg)
        text = msg.text or getattr(msg, "caption", None) or ""
        msg_date = getattr(msg, "date", None)
        if msg_date is not None:
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=datetime.timezone.utc)
            if msg_date < _start_time:
                return
        matched_entries = find_matching_entries(chat_id, username, topic_id, entries)
        if not matched_entries:
            return
        ignored = {str(u).lower().lstrip("@") for u in data.get("ignored_users", [])}
        if ignored:
            sid = getattr(msg, "sender_id", None)
            if sid is None:
                from_id = getattr(msg, "from_id", None)
                sid = getattr(from_id, "user_id", None)
            if sid and str(sid) in ignored:
                log.info("[IGNORE] user_id=%s: sender_id=%s в игнор-листе", user_id, sid)
                return
            has_username_ignores = any(not u.isdigit() for u in ignored)
            if has_username_ignores:
                try:
                    sender = await event.get_sender()
                    uname = (getattr(sender, "username", None) or "").lower()
                    if uname and uname in ignored:
                        log.info("[IGNORE] user_id=%s: @%s в игнор-листе", user_id, uname)
                        return
                except Exception:
                    pass
        found = matches_keywords(text, keywords)
        if not found:
            return
        if data.get("dedup", True):
            sender_id = getattr(msg, "sender_id", None)
            if sender_id is None:
                from_id = getattr(msg, "from_id", None)
                sender_id = getattr(from_id, "user_id", None)
            dedup_key = (bare_id(chat_id), topic_id, sender_id)
            now = datetime.datetime.now(datetime.timezone.utc)
            last_seen = _seen.get(dedup_key)
            if last_seen is not None and (now - last_seen) < _DEDUP_TTL:
                log.info("[DBG] дедупликация user_id=%s: sender_id=%s пропускаем", user_id, sender_id)
                return
            _seen[dedup_key] = now
            expired = [k for k, v in _seen.items() if (now - v) >= _DEDUP_TTL]
            for k in expired:
                del _seen[k]
        link = build_message_link(username, chat_id, msg.id)
        entry_label = format_entry_label(matched_entries[0])
        notify_text = (
            f"🔍 <b>Найдено совпадение!</b>\n\n"
            f"💬 <b>Чат:</b> {entry_label}\n"
            f"🏷 <b>Ключевые слова:</b> {', '.join(f'<code>{k}</code>' for k in found)}\n"
            f"✉️ <b>Текст:</b>\n<i>{text[:300]}{'…' if len(text) > 300 else ''}</i>\n\n"
            f"🔗 <a href='{link}'>Перейти к сообщению</a>"
        )
        try:
            aiogram_bot = getattr(_shared, "aiogram_bot", None)
            if aiogram_bot is not None:
                await aiogram_bot.send_message(user_id, notify_text, parse_mode="HTML", disable_web_page_preview=True)
            else:
                await _shared.notifier.send_message(user_id, notify_text, parse_mode="html", link_preview=False)
            log.info("Уведомление отправлено user_id=%s: %s", user_id, link)
        except Exception as exc:
            log.error("Ошибка отправки уведомления user_id=%s: %s", user_id, exc)

    log.info("Юзербот user_id=%s запущен и слушает события", user_id)
    return client


async def stop_userbot_for(user_id: int):
    import shared as _shared
    client = _shared.userbot_clients.pop(user_id, None)
    if client:
        try:
            await client.disconnect()
            log.info("Юзербот user_id=%s остановлен", user_id)
        except Exception as exc:
            log.error("Ошибка остановки юзербота user_id=%s: %s", user_id, exc)


async def main(aiogram_bot=None):
    from users import get_all_users, get_role
    from shop import check_subscription, get_subscriptions
    import shared as _shared
    if aiogram_bot is not None:
        _shared.notifier = None
        _shared.aiogram_bot = aiogram_bot
        log.info("Нотифаер: используется aiogram Bot")
    else:
        notifier = TelegramClient("notifier_bot", cfg.api_id, cfg.api_hash)
        await notifier.start(bot_token=cfg.bot_token)
        _shared.notifier = notifier
        _shared.aiogram_bot = None
        log.info("Notifier бот запущен (Telethon)")
    all_users = set(cfg.admins)
    for uid, user_data in get_all_users().items():
        all_users.add(int(uid))
    for sub in get_subscriptions():
        all_users.add(int(sub["user_id"]))
    tasks = []
    for uid in all_users:
        if uid in cfg.admins or get_role(uid) in ("owner", "admin", "supplier") or check_subscription(uid):
            tasks.append(start_userbot_for(uid))
    await asyncio.gather(*tasks)
    log.info("Все юзерботы запущены. Слушаем события…")
    if aiogram_bot is None:
        await _shared.notifier.run_until_disconnected()
