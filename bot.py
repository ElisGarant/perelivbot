"""
Telegram-бот — полный файл со всеми обработчиками.
Исправлены: импорты, конфиг, добавлены недостающие обработчики.
"""
import asyncio
import json
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.markdown import hbold, hcode, hitalic

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
    PhoneNumberInvalidError,
)
from telethon.tl.functions.messages import GetDialogFiltersRequest
from telethon.tl.functions.messages import GetForumTopicsRequest

from config import cfg
import users as user_manager
from users import (
    get_role, is_owner, is_admin, is_supplier,
    set_role, get_all_users, add_user, remove_user,
)
from shop import (
    get_products, get_product, add_product, remove_product,
    add_subscription, check_subscription,
    add_transaction, get_transactions,
    get_suppliers, get_supplier_stats,
    load_shop_data, save_shop_data,
)
from userbot import (
    data_file_for, session_name_for,
    load_data as _load_user_data,
    start_userbot_for, stop_userbot_for,
    get_cache,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [BOT] %(levelname)s %(message)s")
log = logging.getLogger(__name__)

bot = Bot(token=cfg.bot_token, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

import shared as _shared


# ============================================================
# FSM состояния
# ============================================================

class Auth(StatesGroup):
    waiting_phone = State()
    waiting_code = State()
    waiting_2fa = State()

class Form(StatesGroup):
    waiting_chat = State()
    waiting_topic = State()
    waiting_keyword = State()
    waiting_ignored_user = State()

class AdminProduct(StatesGroup):
    waiting_name = State()
    waiting_price = State()
    waiting_content = State()

class SupplierProduct(StatesGroup):
    waiting_name = State()
    waiting_price = State()
    waiting_content = State()

class AdminSettings(StatesGroup):
    waiting_broadcast_text = State()
    waiting_subscription_price = State()
    waiting_subscription_days = State()

class RoleManagement(StatesGroup):
    waiting_user_id = State()
    waiting_role = State()


# ============================================================
# Вспомогательные функции
# ============================================================

def check_access(user_id: int) -> bool:
    if is_owner(user_id) or is_admin(user_id) or is_supplier(user_id):
        return True
    return check_subscription(user_id) is not None


def load_data(user_id: int) -> dict:
    return _load_user_data(user_id)


def save_data(user_id: int, data: dict):
    with open(data_file_for(user_id), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    get_cache(user_id).invalidate()


def chat_entry_label(entry) -> str:
    """Человекочитаемая подпись для записи чата."""
    if isinstance(entry, dict):
        title = entry.get("title", str(entry.get("id", "?")))
        topic_name = entry.get("topic_name")
        topic_id = entry.get("topic_id")
        if topic_id is not None:
            return f"{title} › {topic_name or f'топик #{topic_id}'}"
        return title
    return str(entry)


def chat_entry_key(entry) -> str:
    """Уникальный ключ записи для callback_data."""
    if isinstance(entry, dict):
        parts = [str(entry.get("id", ""))]
        if entry.get("topic_id") is not None:
            parts.append(str(entry["topic_id"]))
        return ":".join(parts)
    return str(entry)


def find_entry_by_key(chats: list, key: str) -> int:
    """Найти индекс записи по ключу. Возвращает -1 если не найдено."""
    for i, entry in enumerate(chats):
        if chat_entry_key(entry) == key:
            return i
    return -1


# ============================================================
# Клавиатуры
# ============================================================

def kb_main(active: bool, dedup: bool = True, role: str = "user") -> InlineKeyboardMarkup:
    toggle_text = "🔴 Выключить парсер" if active else "🟢 Включить парсер"
    toggle_cb = "toggle_off" if active else "toggle_on"
    dedup_text = "👤 Уникальные авторы: ВКЛ" if dedup else "👤 Уникальные авторы: ВЫКЛ"
    dedup_cb = "dedup_off" if dedup else "dedup_on"
    rows = [
        [InlineKeyboardButton(text="💬 Чаты", callback_data="menu_chats"),
         InlineKeyboardButton(text="🔑 Ключевые слова", callback_data="menu_keywords")],
        [InlineKeyboardButton(text="🚫 Игнор-лист", callback_data="menu_ignored")],
        [InlineKeyboardButton(text="📊 Статус", callback_data="status")],
        [InlineKeyboardButton(text=toggle_text, callback_data=toggle_cb)],
        [InlineKeyboardButton(text=dedup_text, callback_data=dedup_cb)],
        [InlineKeyboardButton(text="🔑 Сменить аккаунт", callback_data="reauth")],
        [InlineKeyboardButton(text="🧹 Очистить всё", callback_data="confirm_clear")],
    ]
    rows.append([InlineKeyboardButton(text="🛒 Магазин", callback_data="menu_shop_user")])
    if role in ("owner", "admin"):
        rows.append([InlineKeyboardButton(text="🛡 Админ-панель", callback_data="menu_admin")])
    if role in ("owner", "admin", "supplier"):
        rows.append([InlineKeyboardButton(text="📦 Поставщик", callback_data="menu_supplier")])
    if role == "owner":
        rows.append([InlineKeyboardButton(text="👥 Управление ролями", callback_data="menu_roles")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_start_auth() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📱 Авторизоваться", callback_data="start_auth")],
        [InlineKeyboardButton(text="🛒 Магазин", callback_data="menu_shop_user")],
    ])


CHATS_PAGE_SIZE = 50

def kb_chats(chats: list, page: int = 0) -> InlineKeyboardMarkup:
    total = len(chats)
    start = page * CHATS_PAGE_SIZE
    end = start + CHATS_PAGE_SIZE
    page_chats = chats[start:end]
    rows = [
        [InlineKeyboardButton(
            text=f"❌  {chat_entry_label(c)}",
            callback_data=f"del_chat:{chat_entry_key(c)}"
        )]
        for c in page_chats
    ]
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"chats_page:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"chats_page:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(text="➕ Добавить чат", callback_data="add_chat"),
        InlineKeyboardButton(text="📁 Из папки", callback_data="add_from_folder"),
    ])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_keywords(keywords: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"❌  {k}", callback_data=f"del_kw:{k}")]
        for k in keywords
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить слово", callback_data="add_kw")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_ignored(ignored: list) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"❌  {u}", callback_data=f"del_ignored:{u}")]
        for u in ignored
    ]
    rows.append([InlineKeyboardButton(text="➕ Добавить в игнор", callback_data="add_ignored")])
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_cancel_auth() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_auth")],
    ])


def kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="back_main")],
    ])


def kb_confirm_clear() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить всё", callback_data="do_clear"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="back_main"),
        ]
    ])


def kb_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Главное меню", callback_data="back_main")],
    ])


def kb_folder_select(folders: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"📁 {f['title']}", callback_data=f"select_folder:{f['id']}")]
        for f in folders
    ]
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="menu_chats")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_topic_select(topics: list[dict], chat_id_str: str) -> InlineKeyboardMarkup:
    rows = []
    for t in topics[:40]:
        cb = f"select_topic:{chat_id_str}:{t['id']}"
        rows.append([InlineKeyboardButton(text=f"💬 {t['title']}", callback_data=cb)])
    rows.append([InlineKeyboardButton(text="🌐 Все топики", callback_data=f"select_topic:{chat_id_str}:all")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="back_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ============================================================
# Клавиатуры магазина
# ============================================================

def kb_shop_admin() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Управление товарами", callback_data="admin_products")],
        [InlineKeyboardButton(text="💳 Подписки и пользователи", callback_data="admin_users")],
        [InlineKeyboardButton(text="📤 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📊 Сделки", callback_data="admin_transactions")],
        [InlineKeyboardButton(text="👥 Поставщики", callback_data="admin_suppliers")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])


def kb_shop_user() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Купить товар", callback_data="menu_shop_user")],
        [InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="👤 Моя подписка", callback_data="my_subscription")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])


def kb_products_list_admin(products: list) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        rows.append([InlineKeyboardButton(
            text=f"❌ {p['name']} — {p['price']}₽",
            callback_data=f"del_product:{p['id']}"
        )])
    rows.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_products_list_user(products: list) -> InlineKeyboardMarkup:
    rows = []
    for p in products:
        rows.append([InlineKeyboardButton(
            text=f"🛒 {p['name']} — {p['price']}₽",
            callback_data=f"buy_product:{p['id']}"
        )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_shop_user")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_subscription_plans() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 7 дней — 99₽", callback_data="sub_buy_7")],
        [InlineKeyboardButton(text="📅 30 дней — 299₽", callback_data="sub_buy_30")],
        [InlineKeyboardButton(text="📅 90 дней — 699₽", callback_data="sub_buy_90")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_shop_user")],
    ])


def kb_roles_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Назначить роль", callback_data="role_assign")],
        [InlineKeyboardButton(text="📋 Список пользователей", callback_data="role_list")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])


# ============================================================
# Тексты
# ============================================================

def text_main(active: bool, name: str, dedup: bool = True, role: str = "user") -> str:
    p_status = "🟢 <b>Активен</b>" if active else "🔴 <b>Остановлен</b>"
    d_status = "ВКЛ 👤" if dedup else "ВЫКЛ 👥"
    role_ru = {"owner": "владелец", "admin": "админ", "supplier": "поставщик", "user": "пользователь"}
    role_text = role_ru.get(role, role)
    return (
        f"👋 Привет, {hbold(name)}!\n\n"
        f"🎭 Роль: {role_text}\n"
        f"🤖 <b>Парсер:</b> {p_status}\n"
        f"👤 <b>Уникальные авторы:</b> {d_status}\n\n"
        "Выбери раздел:"
    )


def text_not_authorized(name: str) -> str:
    return (
        f"👋 Привет, {hbold(name)}!\n\n"
        "⚠️ <b>Аккаунт Telegram не авторизован.</b>\n\n"
        "Юзербот не может парсить чаты без авторизации.\n"
        "Нажми кнопку ниже, чтобы войти в аккаунт:"
    )


def text_no_access() -> str:
    return (
        "⛔ <b>Нет доступа.</b>\n\n"
        "Обратись к администратору для получения доступа к боту.\n"
        "Или купи подписку в магазине: /shop"
    )


def text_status(active: bool, dedup: bool, chats: list, keywords: list,
                ignored: list, role: str) -> str:
    p_status = "🟢 Активен" if active else "🔴 Остановлен"
    d_status = "ВКЛ" if dedup else "ВЫКЛ"
    lines = [
        "📊 <b>Статус парсера</b>\n",
        f"🤖 Парсер: {p_status}",
        f"👤 Уникальные авторы: {d_status}",
        f"💬 Чатов отслеживается: <b>{len(chats)}</b>",
        f"🔑 Ключевых слов: <b>{len(keywords)}</b>",
        f"🚫 В игнор-листе: <b>{len(ignored)}</b>",
        f"🎭 Роль: {role}",
        f"\n💬 Чаты: {', '.join(chat_entry_label(c) for c in chats[:10])}"
            if chats else "\n💬 Чаты: нет",
        f"\n🔑 Слова: {', '.join(keywords[:10])}" if keywords else "\n🔑 Слова: нет",
        f"\n🚫 Игнор: {', '.join(str(u) for u in ignored[:10])}" if ignored else "",
    ]
    return "\n".join(lines)


# ============================================================
# /start и /shop
# ============================================================

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not check_access(uid):
        await message.answer(text_no_access(), reply_markup=kb_shop_user())
        return
    await state.clear()
    role = get_role(uid)
    data = load_data(uid)
    
    # Проверяем есть ли сессия юзербота
    session_path = f"{session_name_for(uid)}.session"
    session_exists = Path(session_path).exists()
    
    if not session_exists and check_access(uid):
        await message.answer(
            text_not_authorized(message.from_user.first_name),
            reply_markup=kb_start_auth()
        )
        return
    
    await message.answer(
        text_main(data.get("active", True), message.from_user.first_name,
                  data.get("dedup", True), role),
        reply_markup=kb_main(data.get("active", True), data.get("dedup", True), role)
    )


@router.message(Command("shop"))
async def cmd_shop(message: Message, state: FSMContext):
    uid = message.from_user.id
    await state.clear()
    if is_admin(uid):
        await message.answer("🛒 <b>Магазин (админ):</b>", reply_markup=kb_shop_admin())
    elif is_supplier(uid):
        await message.answer("🛒 <b>Магазин (поставщик):</b>", reply_markup=kb_shop_user())
    else:
        await message.answer("🛒 <b>Магазин:</b>", reply_markup=kb_shop_user())


# ============================================================
# Команды администратора: /add, /remove
# ============================================================

@router.message(Command("add"))
async def cmd_add(message: Message):
    uid = message.from_user.id
    if not is_admin(uid):
        await message.answer("⛔ Только администраторы могут добавлять пользователей.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /add <telegram_id>\nПример: /add 123456789")
        return
    target_id = int(parts[1])
    if target_id in cfg.admins:
        await message.answer(f"ℹ️ Пользователь <code>{target_id}</code> уже является администратором.")
        return
    add_user(target_id)
    await message.answer(f"✅ Пользователь <code>{target_id}</code> добавлен.")
    try:
        await bot.send_message(target_id, "✅ <b>Тебе выдан доступ!</b>\n\nНапиши /start.")
    except Exception:
        await message.answer(f"⚠️ Не удалось уведомить пользователя {target_id}.")


@router.message(Command("remove"))
async def cmd_remove(message: Message):
    uid = message.from_user.id
    if not is_admin(uid):
        await message.answer("⛔ Только администраторы могут удалять пользователей.")
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: /remove <telegram_id>\nПример: /remove 123456789")
        return
    target_id = int(parts[1])
    if target_id in cfg.admins:
        await message.answer("⛔ Нельзя удалить администратора.")
        return
    remove_user(target_id)
    if target_id in _shared.userbot_clients:
        await stop_userbot_for(target_id)
    await message.answer(f"✅ Пользователь <code>{target_id}</code> удалён.")


# ============================================================
# НАВИГАЦИЯ — back_main + status
# ============================================================

@router.callback_query(F.data == "back_main")
async def cb_back_main(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        await call.message.edit_text(text_no_access(), reply_markup=kb_shop_user())
        await call.answer()
        return
    await state.clear()
    data = load_data(uid)
    role = get_role(uid)
    await call.message.edit_text(
        text_main(data.get("active", True), call.from_user.first_name,
                  data.get("dedup", True), role),
        reply_markup=kb_main(data.get("active", True), data.get("dedup", True), role)
    )
    await call.answer()


@router.callback_query(F.data == "status")
async def cb_status(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    await state.clear()
    data = load_data(uid)
    role = get_role(uid)
    await call.message.edit_text(
        text_status(
            data.get("active", True), data.get("dedup", True),
            data.get("chats", []), data.get("keywords", []),
            data.get("ignored_users", []), role
        ),
        reply_markup=kb_back()
    )
    await call.answer()


# ============================================================
# ОБРАБОТЧИК 1-2: toggle_on / toggle_off
# ============================================================

@router.callback_query(F.data == "toggle_on")
async def cb_toggle_on(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    data = load_data(uid)
    data["active"] = True
    save_data(uid, data)
    
    # Запускаем юзербот если сессия есть и ещё не запущен
    if uid not in _shared.userbot_clients:
        session_path = f"{session_name_for(uid)}.session"
        if Path(session_path).exists():
            asyncio.create_task(start_userbot_for(uid))
    
    role = get_role(uid)
    await call.message.edit_text(
        text_main(True, call.from_user.first_name, data.get("dedup", True), role),
        reply_markup=kb_main(True, data.get("dedup", True), role)
    )
    await call.answer("✅ Парсер включён")


@router.callback_query(F.data == "toggle_off")
async def cb_toggle_off(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    data = load_data(uid)
    data["active"] = False
    save_data(uid, data)
    
    # Останавливаем юзербот если запущен
    if uid in _shared.userbot_clients:
        await stop_userbot_for(uid)
    
    role = get_role(uid)
    await call.message.edit_text(
        text_main(False, call.from_user.first_name, data.get("dedup", True), role),
        reply_markup=kb_main(False, data.get("dedup", True), role)
    )
    await call.answer("🔴 Парсер выключен")


# ============================================================
# ОБРАБОТЧИК 3-4: dedup_on / dedup_off
# ============================================================

@router.callback_query(F.data == "dedup_on")
async def cb_dedup_on(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    data = load_data(uid)
    data["dedup"] = True
    save_data(uid, data)
    role = get_role(uid)
    await call.message.edit_text(
        text_main(data.get("active", True), call.from_user.first_name, True, role),
        reply_markup=kb_main(data.get("active", True), True, role)
    )
    await call.answer("✅ Дедупликация включена")


@router.callback_query(F.data == "dedup_off")
async def cb_dedup_off(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    data = load_data(uid)
    data["dedup"] = False
    save_data(uid, data)
    role = get_role(uid)
    await call.message.edit_text(
        text_main(data.get("active", True), call.from_user.first_name, False, role),
        reply_markup=kb_main(data.get("active", True), False, role)
    )
    await call.answer("👥 Дедупликация выключена")


# ============================================================
# ОБРАБОТЧИК 5-8: Ключевые слова (menu_keywords + add_kw + got_keyword + del_kw)
# ============================================================

@router.callback_query(F.data == "menu_keywords")
async def cb_menu_keywords(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    await state.clear()
    data = load_data(uid)
    keywords = data.get("keywords", [])
    body = f"🔑 <b>Ключевые слова</b> ({len(keywords)})\n\n" + "\n".join(f"• {hcode(k)}" for k in keywords) if keywords else "🔑 <b>Ключевые слова</b>\n\nПусто."
    await call.message.edit_text(body, reply_markup=kb_keywords(keywords))
    await call.answer()


@router.callback_query(F.data == "add_kw")
async def cb_add_kw(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    await state.set_state(Form.waiting_keyword)
    await call.message.edit_text("🔑 <b>Добавление ключевого слова</b>\n\nОтправь слово или фразу.", reply_markup=kb_cancel())
    await call.answer()


@router.message(Form.waiting_keyword)
async def got_keyword(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not check_access(uid):
        return
    kw = (message.text or "").strip().lower()
    if not kw:
        await message.answer("⚠️ Пустой ввод.", reply_markup=kb_cancel())
        return
    data = load_data(uid)
    if kw in [k.lower() for k in data["keywords"]]:
        await message.answer(f"⚠️ Слово {hcode(kw)} уже есть в списке.", reply_markup=kb_cancel())
        return
    data["keywords"].append(kw)
    save_data(uid, data)
    await state.clear()
    keywords = data["keywords"]
    body = f"🔑 <b>Ключевые слова</b> ({len(keywords)})\n\n" + "\n".join(f"• {hcode(k)}" for k in keywords)
    await message.answer(f"✅ Слово {hcode(kw)} добавлено!\n\n{body}", reply_markup=kb_keywords(keywords))


@router.callback_query(F.data.startswith("del_kw:"))
async def cb_del_kw(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    kw = call.data.split(":", 1)[1]
    data = load_data(uid)
    data["keywords"] = [k for k in data["keywords"] if k != kw]
    save_data(uid, data)
    keywords = data["keywords"]
    body = f"🔑 <b>Ключевые слова</b> ({len(keywords)})\n\n" + "\n".join(f"• {hcode(k)}" for k in keywords) if keywords else "🔑 <b>Ключевые слова</b>\n\nПусто."
    await call.message.edit_text(body, reply_markup=kb_keywords(keywords))
    await call.answer(f"🗑 «{kw}» удалено")


# ============================================================
# ОБРАБОТЧИК 9-12: Игнор-лист (menu_ignored + add_ignored + got_ignored_user + del_ignored)
# ============================================================

@router.callback_query(F.data == "menu_ignored")
async def cb_menu_ignored(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    await state.clear()
    data = load_data(uid)
    ignored = data.get("ignored_users", [])
    body = f"🚫 <b>Игнор-лист</b> ({len(ignored)})\n\n" + "\n".join(f"• {hcode(u)}" for u in ignored) if ignored else "🚫 <b>Игнор-лист</b>\n\nПусто."
    await call.message.edit_text(body, reply_markup=kb_ignored(ignored))
    await call.answer()


@router.callback_query(F.data == "add_ignored")
async def cb_add_ignored(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    await state.set_state(Form.waiting_ignored_user)
    await call.message.edit_text("🚫 <b>Добавление в игнор-лист</b>\n\nОтправь @username или ID пользователя.", reply_markup=kb_cancel())
    await call.answer()


@router.message(Form.waiting_ignored_user)
async def got_ignored_user(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not check_access(uid):
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("⚠️ Пустой ввод.", reply_markup=kb_cancel())
        return
    try:
        entry = str(int(raw))
    except ValueError:
        entry = ("" if raw.startswith("@") else "@") + raw.lstrip("@").lower()
    data = load_data(uid)
    ignored = data.setdefault("ignored_users", [])
    normalized_existing = [str(e).lower().lstrip("@") for e in ignored]
    if entry.lower().lstrip("@") in normalized_existing:
        await message.answer(f"⚠️ {hcode(entry)} уже в игнор-листе.", reply_markup=kb_cancel())
        return
    ignored.append(entry)
    save_data(uid, data)
    await state.clear()
    body = f"🚫 <b>Игнор-лист</b> ({len(ignored)})\n\n" + "\n".join(f"• {hcode(u)}" for u in ignored) if ignored else "🚫 <b>Игнор-лист</b>\n\nПусто."
    await message.answer(f"✅ {hcode(entry)} добавлен в игнор-лист!\n\n{body}", reply_markup=kb_ignored(ignored))


@router.callback_query(F.data.startswith("del_ignored:"))
async def cb_del_ignored(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    entry = call.data.split(":", 1)[1]
    data = load_data(uid)
    before = len(data.get("ignored_users", []))
    data["ignored_users"] = [u for u in data.get("ignored_users", []) if str(u) != entry]
    save_data(uid, data)
    ignored = data["ignored_users"]
    body = f"🚫 <b>Игнор-лист</b> ({len(ignored)})\n\n" + "\n".join(f"• {hcode(u)}" for u in ignored) if ignored else "🚫 <b>Игнор-лист</b>\n\nПусто."
    await call.message.edit_text(body, reply_markup=kb_ignored(ignored))
    await call.answer(f"✅ {entry} удалён из игнора" if len(ignored) < before else "⚠️ Не найдено")


# ============================================================
# ОБРАБОТЧИКИ ЧАТОВ, ПАПОК, ТОПИКОВ, ОЧИСТКИ, АВТОРИЗАЦИИ
# ============================================================

@router.callback_query(F.data == "menu_chats")
async def cb_menu_chats(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    await state.clear()
    data = load_data(uid)
    chats = data.get("chats", [])
    body = f"💬 <b>Чаты</b> ({len(chats)})" if chats else "💬 <b>Чаты</b>\n\nПусто."
    await call.message.edit_text(body, reply_markup=kb_chats(chats))
    await call.answer()


@router.callback_query(F.data == "add_chat")
async def cb_add_chat(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    await state.set_state(Form.waiting_chat)
    await call.message.edit_text("Отправь @username или ID чата:", reply_markup=kb_cancel())
    await call.answer()


@router.message(Form.waiting_chat)
async def got_chat(message: Message, state: FSMContext):
    uid = message.from_user.id
    if not check_access(uid):
        return
    raw = (message.text or "").strip()
    if not raw:
        await message.answer("Не может быть пустым.", reply_markup=kb_cancel())
        return
    chat_input = raw.lstrip("@")
    data = load_data(uid)
    chats = data.get("chats", [])
    for c in chats:
        existing = c if isinstance(c, str) else str(c.get("id", ""))
        if existing.lstrip("@").lower() == chat_input.lower():
            await message.answer("Этот чат уже добавлен.", reply_markup=kb_chats(chats))
            await state.clear()
            return
    entry = chat_input
    client = _shared.userbot_clients.get(uid)
    if client:
        try:
            if chat_input.lstrip("-").isdigit():
                entity = await client.get_entity(int(chat_input))
            else:
                entity = await client.get_entity(chat_input)
            title = getattr(entity, "title", None) or getattr(entity, "first_name", chat_input)
            real_id = getattr(entity, "id", chat_input)
            entry = {"id": str(real_id), "title": title}
        except Exception as exc:
            log.warning("Не удалось получить сущность чата %s: %s", chat_input, exc)
    chats.append(entry)
    data["chats"] = chats
    save_data(uid, data)
    await message.answer(f"✅ Чат добавлен: {hcode(chat_entry_label(entry))}", reply_markup=kb_chats(chats))
    await state.clear()


@router.callback_query(F.data.startswith("del_chat:"))
async def cb_del_chat(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    key = call.data.split(":", 1)[1]
    data = load_data(uid)
    chats = data.get("chats", [])
    data["chats"] = [c for c in chats if chat_entry_key(c) != key]
    save_data(uid, data)
    new_chats = data["chats"]
    body = f"💬 <b>Чаты</b> ({len(new_chats)})" if new_chats else "💬 <b>Чаты</b>\n\nПусто."
    await call.message.edit_text(body, reply_markup=kb_chats(new_chats))
    await call.answer("Удалено")


@router.callback_query(F.data.startswith("chats_page:"))
async def cb_chats_page(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    page = int(call.data.split(":")[1])
    data = load_data(uid)
    chats = data.get("chats", [])
    body = f"💬 <b>Чаты</b> ({len(chats)})" if chats else "💬 Пусто."
    await call.message.edit_text(body, reply_markup=kb_chats(chats, page))
    await call.answer()


@router.callback_query(F.data == "add_from_folder")
async def cb_add_from_folder(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    client = _shared.userbot_clients.get(uid)
    if not client:
        await call.answer("⚠️ Юзербот не запущен.", show_alert=True)
        return
    try:
        result = await client(GetDialogFiltersRequest())
        folders = []
        for f in result.filters:
            folders.append({"id": f.id, "title": getattr(f, "title", f"Папка {f.id}")})
        if not folders:
            await call.answer("Папки не найдены.", show_alert=True)
            return
        await call.message.edit_text("📁 Выбери папку:", reply_markup=kb_folder_select(folders))
        await call.answer()
    except Exception as exc:
        log.error("Ошибка получения папок: %s", exc)
        await call.answer("⚠️ Ошибка.", show_alert=True)


@router.callback_query(F.data.startswith("select_folder:"))
async def cb_select_folder(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        return
    fid = int(call.data.split(":")[1])
    client = _shared.userbot_clients.get(uid)
    if not client:
        await call.answer("⚠️ Юзербот не запущен.", show_alert=True)
        return
    try:
        dialogs = await client.get_dialogs()
        data = load_data(uid)
        existing_ids = set()
        for c in data.get("chats", []):
            cid = c if isinstance(c, str) else str(c.get("id", ""))
            existing_ids.add(cid.lstrip("@").lower())
        added = 0
        for d in dialogs:
            did = str(d.id) if hasattr(d, "id") else ""
            dname = getattr(d.entity, "username", "") or did
            check_val = (dname or did).lstrip("@").lower()
            if check_val in existing_ids:
                continue
            title = getattr(d.entity, "title", None) or getattr(d.entity, "first_name", did)
            entry = {"id": did or dname, "title": title}
            data.setdefault("chats", []).append(entry)
            existing_ids.add(check_val)
            added += 1
        save_data(uid, data)
        chats = data.get("chats", [])
        body = f"✅ Добавлено {added} чатов.\n\n💬 <b>Чаты</b> ({len(chats)})"
        await call.message.edit_text(body, reply_markup=kb_chats(chats))
        await call.answer(f"Добавлено: {added}")
    except Exception as exc:
        log.error("Ошибка добавления из папки: %s", exc)
        await call.answer("⚠️ Ошибка.", show_alert=True)


@router.callback_query(F.data.startswith("select_topic:"))
async def cb_select_topic(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        return
    parts = call.data.split(":")
    chat_id_str = parts[1]
    topic_val = parts[2] if len(parts) > 2 else "all"
    client = _shared.userbot_clients.get(uid)
    if topic_val == "all":
        data = load_data(uid)
        for c in data.get("chats", []):
            if isinstance(c, dict) and str(c.get("id", "")) == chat_id_str:
                c.pop("topic_id", None)
                c.pop("topic_name", None)
                break
        save_data(uid, data)
        await call.message.edit_text(f"✅ Чат <code>{chat_id_str}</code> — все топики.", reply_markup=kb_back())
        await call.answer()
        return
    topic_id = int(topic_val)
    topic_name = f"топик #{topic_id}"
    if client:
        try:
            chat_id_int = int(chat_id_str)
            result = await client(GetForumTopicsRequest(peer=chat_id_int, offset_date=None, offset_id=0, limit=100))
            for t in result.topics:
                if getattr(t, "id", None) == topic_id:
                    topic_name = getattr(t, "title", topic_name)
                    break
        except Exception as exc:
            log.warning("Не удалось получить название топика: %s", exc)
    data = load_data(uid)
    found = False
    for c in data.get("chats", []):
        if isinstance(c, dict) and str(c.get("id", "")) == chat_id_str:
            c["topic_id"] = topic_id
            c["topic_name"] = topic_name
            found = True
            break
    if not found:
        data.setdefault("chats", []).append({"id": chat_id_str, "title": chat_id_str, "topic_id": topic_id, "topic_name": topic_name})
    save_data(uid, data)
    label = f"{chat_id_str} > {topic_name}"
    await call.message.edit_text(f"✅ Добавлен топик:\n<b>{hcode(label)}</b>", reply_markup=kb_back())
    await call.answer()


@router.callback_query(F.data == "confirm_clear")
async def cb_confirm_clear(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    await call.message.edit_text(
        "⚠️ <b>Очистить все данные?</b>\n\nБудут удалены:\n- Все чаты\n- Все ключевые слова\n- Игнор-лист\n- Настройки дедупликации\n\n<i>Это действие необратимо!</i>",
        reply_markup=kb_confirm_clear()
    )
    await call.answer()


@router.callback_query(F.data == "do_clear")
async def cb_do_clear(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        return
    empty = {"chats": [], "keywords": [], "ignored_users": [], "active": True, "dedup": True}
    save_data(uid, empty)
    await call.message.edit_text("✅ <b>Все данные очищены.</b>", reply_markup=kb_back())
    await call.answer("Очищено")


# ============================================================
# АВТОРИЗАЦИЯ ЧЕРЕЗ БОТА
# ============================================================

@router.callback_query(F.data == "reauth")
async def cb_reauth(call: CallbackQuery):
    uid = call.from_user.id
    if not check_access(uid):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    sp = f"{session_name_for(uid)}.session"
    session_exists = Path(sp).exists()
    rows = [[InlineKeyboardButton(text="🔄 Авторизоваться заново" if session_exists else "📱 Авторизоваться", callback_data="start_auth")]]
    rows.append([InlineKeyboardButton(text="◀️ Назад", callback_data="back_main")])
    await call.message.edit_text(
        f"🔑 <b>Авторизация Telegram</b>\n\nСтатус: {'✅ Авторизован' if session_exists else '❌ Не авторизован'}\nСессия: {'найдена' if session_exists else 'не найдена'}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows)
    )
    await call.answer()


@router.callback_query(F.data == "start_auth")
async def cb_start_auth(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    if not check_access(uid):
        return
    await state.set_state(Auth.waiting_phone)
    await call.message.edit_text(
        "📱 <b>Авторизация Telegram</b>\n\nОтправь номер телефона в международном формате:\n<i>Пример: +79991234567</i>",
        reply_markup=kb_cancel_auth()
    )
    await call.answer()


@router.callback_query(F.data == "cancel_auth")
async def cb_cancel_auth(call: CallbackQuery, state: FSMContext):
    uid = call.from_user.id
    await state.clear()
    role = get_role(uid)
    data = load_data(uid)
    sp = f"{session_name_for(uid)}.session"
    session_exists = Path(sp).exists()
    active = data.get("active", True) and session_exists
    await call.message.edit_text(
        text_main(active, call.from_user.first_name, data.get("dedup", True), role),
        reply_markup=kb_main(active, data.get("dedup", True), role)
    )
    await call.answer()


@router.message(Auth.waiting_phone)
async def auth_phone(message: Message, state: FSMContext):
    uid = message.from_user.id
    phone = (message.text or "").strip()
    if not phone.startswith("+"):
        await message.answer("⚠️ Номер должен начинаться с +\nПример: +79991234567")
        return
    await state.update_data(phone=phone)
    sp = session_name_for(uid)
    for ext in [".session", ".session-journal"]:
        p = Path(f"{sp}{ext}")
        if p.exists():
            p.unlink()
    client = TelegramClient(sp, cfg.api_id, cfg.api_hash)
    try:
        await client.connect()
        result = await client.send_code_request(phone)
        if not hasattr(_shared, "_auth_clients"):
            _shared._auth_clients = {}
        _shared._auth_clients[uid] = {"client": client, "phone": phone, "phone_code_hash": result.phone_code_hash}
        await state.set_state(Auth.waiting_code)
        await message.answer("✅ Код отправлен!\n\nВведи код из Telegram:", reply_markup=kb_cancel_auth())
    except PhoneNumberInvalidError:
        await message.answer("❌ Неверный номер телефона.")
        await state.clear()
        try:
            await client.disconnect()
        except:
            pass
    except Exception as exc:
        log.error("Ошибка отправки кода: %s", exc)
        await message.answer("❌ Ошибка.")
        await state.clear()
        try:
            await client.disconnect()
        except:
            pass


@router.message(Auth.waiting_code)
async def auth_code(message: Message, state: FSMContext):
    uid = message.from_user.id
    code = (message.text or "").strip()
    auth_data = _shared._auth_clients.get(uid) if hasattr(_shared, "_auth_clients") else None
    if not auth_data:
        await message.answer("❌ Сессия авторизации истекла. Начни заново.")
        await state.clear()
        return
    client = auth_data["client"]
    phone = auth_data["phone"]
    pch = auth_data["phone_code_hash"]
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=pch)
        except SessionPasswordNeededError:
            await state.set_state(Auth.waiting_2fa)
            await message.answer("🔐 Введи пароль двухфакторной аутентификации:", reply_markup=kb_cancel_auth())
            return
        me = await client.get_me()
        _shared.userbot_clients[uid] = client
        del _shared._auth_clients[uid]
        await state.clear()
        role = get_role(uid)
        data = load_data(uid)
        await message.answer(
            f"✅ Авторизован как @{getattr(me, 'username', 'без username')}\nИмя: {getattr(me, 'first_name', '?')}",
            reply_markup=kb_main(data.get("active", True), data.get("dedup", True), role)
        )
        asyncio.create_task(start_userbot_for(uid))
        log.info("Юзербот user_id=%s авторизован через бота", uid)
    except PhoneCodeInvalidError:
        await message.answer("❌ Неверный код.")
    except PhoneCodeExpiredError:
        await message.answer("❌ Код истёк. Начни заново.")
        await state.clear()
        del _shared._auth_clients[uid]
        try:
            await client.disconnect()
        except:
            pass
    except Exception as exc:
        log.error("Ошибка входа: %s", exc)
        await message.answer("❌ Ошибка авторизации.")


@router.message(Auth.waiting_2fa)
async def auth_2fa(message: Message, state: FSMContext):
    uid = message.from_user.id
    password = (message.text or "").strip()
    auth_data = _shared._auth_clients.get(uid) if hasattr(_shared, "_auth_clients") else None
    if not auth_data:
        await message.answer("❌ Сессия истекла.")
        await state.clear()
        return
    client = auth_data["client"]
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        _shared.userbot_clients[uid] = client
        del _shared._auth_clients[uid]
        await state.clear()
        role = get_role(uid)
        data = load_data(uid)
        await message.answer(
            f"✅ Авторизован как @{getattr(me, 'username', 'без username')}",
            reply_markup=kb_main(data.get("active", True), data.get("dedup", True), role)
        )
        asyncio.create_task(start_userbot_for(uid))
        log.info("Юзербот user_id=%s авторизован через бота (2FA)", uid)
    except Exception as exc:
        log.error("Ошибка 2FA: %s", exc)
        await message.answer("❌ Неверный пароль или ошибка.")


# ============================================================
# АДМИН-ПАНЕЛЬ, МАГАЗИН, ПОСТАВЩИКИ, РОЛИ
# ============================================================

@router.callback_query(F.data == "menu_admin")
async def menu_admin(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        await call.answer("⛔ Только для администраторов.", show_alert=True)
        return
    await call.message.edit_text("🛡 Админ-панель", reply_markup=kb_shop_admin())
    await call.answer()


@router.callback_query(F.data == "admin_products")
async def admin_products(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    products = get_products()
    rows = [[InlineKeyboardButton(text=f"❌ {p['name']} — {p['price']}₽", callback_data=f"del_product:{p['id']}")] for p in products]
    rows.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_admin")])
    body = "📦 Товары:" if products else "📦 Товаров нет."
    await call.message.edit_text(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data == "admin_add_product")
async def admin_add_product(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminProduct.waiting_name)
    await call.message.edit_text("Введите название товара:")
    await call.answer()


@router.message(AdminProduct.waiting_name)
async def admin_add_product_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(AdminProduct.waiting_price)
    await message.answer("Введите цену:")


@router.message(AdminProduct.waiting_price)
async def admin_add_product_price(message: Message, state: FSMContext):
    try:
        price = float(message.text)
    except ValueError:
        await message.answer("Цена должна быть числом.")
        return
    await state.update_data(price=price)
    await state.set_state(AdminProduct.waiting_content)
    await message.answer("Введите содержимое для автовыдачи:")


@router.message(AdminProduct.waiting_content)
async def admin_add_product_content(message: Message, state: FSMContext):
    d = await state.get_data()
    add_product(d["name"], d["price"], message.text)
    await state.clear()
    await message.answer("✅ Товар добавлен.", reply_markup=kb_back())


@router.callback_query(F.data.startswith("del_product:"))
async def del_product(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.split(":")[1])
    remove_product(pid)
    await call.answer("Удалено")
    products = get_products()
    rows = [[InlineKeyboardButton(text=f"❌ {p['name']} — {p['price']}₽", callback_data=f"del_product:{p['id']}")] for p in products]
    rows.append([InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin_add_product")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_admin")])
    body = "📦 Товары:" if products else "📦 Товаров нет."
    await call.message.edit_text(body, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data == "admin_users")
async def admin_users(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    users = get_all_users()
    lines = []
    for uid, user in users.items():
        role = user["role"]
        sub = check_subscription(int(uid))
        sub_status = "активна" if sub else "нет"
        lines.append(f"ID: {uid} | {role} | подписка: {sub_status}")
    await call.message.edit_text(
        "👥 Пользователи:\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_admin")]])
    )
    await call.answer()


@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Изменить текст рассылки", callback_data="admin_edit_broadcast_text")],
        [InlineKeyboardButton(text="💰 Изменить цену подписки", callback_data="admin_edit_price")],
        [InlineKeyboardButton(text="📅 Изменить дни подписки", callback_data="admin_edit_days")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_admin")],
    ])
    await call.message.edit_text("📤 Настройки рассылки", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "admin_edit_broadcast_text")
async def admin_edit_broadcast_text(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSettings.waiting_broadcast_text)
    await call.message.edit_text("Введите новый текст рассылки:")
    await call.answer()


@router.message(AdminSettings.waiting_broadcast_text)
async def admin_edit_broadcast_text_finish(message: Message, state: FSMContext):
    data = load_shop_data()
    data["broadcast_text"] = message.text
    save_shop_data(data)
    await state.clear()
    await message.answer("✅ Текст обновлён.", reply_markup=kb_back())


@router.callback_query(F.data == "admin_edit_price")
async def admin_edit_price(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSettings.waiting_subscription_price)
    await call.message.edit_text("Введите новую цену подписки (в рублях):")
    await call.answer()


@router.message(AdminSettings.waiting_subscription_price)
async def admin_edit_price_finish(message: Message, state: FSMContext):
    try:
        price = float(message.text)
    except ValueError:
        await message.answer("Цена должна быть числом.")
        return
    data = load_shop_data()
    data["subscription_price"] = price
    save_shop_data(data)
    await state.clear()
    await message.answer("✅ Цена обновлена.", reply_markup=kb_back())


@router.callback_query(F.data == "admin_edit_days")
async def admin_edit_days(call: CallbackQuery, state: FSMContext):
    if not is_admin(call.from_user.id):
        return
    await state.set_state(AdminSettings.waiting_subscription_days)
    await call.message.edit_text("Введите количество дней подписки:")
    await call.answer()


@router.message(AdminSettings.waiting_subscription_days)
async def admin_edit_days_finish(message: Message, state: FSMContext):
    try:
        days = int(message.text)
    except ValueError:
        await message.answer("Дней должно быть целое число.")
        return
    data = load_shop_data()
    data["subscription_days"] = days
    save_shop_data(data)
    await state.clear()
    await message.answer("✅ Дни обновлены.", reply_markup=kb_back())


@router.callback_query(F.data == "admin_transactions")
async def admin_transactions(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    txs = get_transactions()
    if not txs:
        await call.message.edit_text("Сделок нет.", reply_markup=kb_back())
        await call.answer()
        return
    lines = []
    for tx in txs:
        supplier = tx.get("supplier_id") if tx.get("supplier_id") else "—"
        lines.append(f"#{tx['id']} | покупатель: {tx['user_id']} | товар: {tx['product_id']} | сумма: {tx['amount']} | поставщик: {supplier}")
    await call.message.edit_text("📊 Все сделки:\n" + "\n".join(lines), reply_markup=kb_back())
    await call.answer()


@router.callback_query(F.data == "admin_suppliers")
async def admin_suppliers(call: CallbackQuery):
    if not is_admin(call.from_user.id):
        return
    suppliers = get_suppliers()
    if not suppliers:
        await call.message.edit_text("Поставщиков нет.", reply_markup=kb_back())
        await call.answer()
        return
    lines = []
    for sid in suppliers:
        stats = get_supplier_stats(sid)
        lines.append(f"ID: {sid} | товаров: {stats['products_count']} | сделок: {stats['transactions_count']} | сумма: {stats['total_amount']}₽")
    await call.message.edit_text(
        "👥 Поставщики:\n" + "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_admin")]])
    )
    await call.answer()


@router.callback_query(F.data == "menu_supplier")
async def menu_supplier(call: CallbackQuery):
    if not is_supplier(call.from_user.id):
        await call.answer("⛔ Нет доступа.", show_alert=True)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить товар", callback_data="supplier_add_product")],
        [InlineKeyboardButton(text="📦 Мои товары", callback_data="supplier_my_products")],
        [InlineKeyboardButton(text="📊 Мои сделки", callback_data="supplier_my_transactions")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")],
    ])
    await call.message.edit_text("📦 Панель поставщика", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data == "supplier_add_product")
async def supplier_add_product(call: CallbackQuery, state: FSMContext):
    if not is_supplier(call.from_user.id):
        return
    await state.set_state(SupplierProduct.waiting_name)
    await call.message.edit_text("Введите название товара:")
    await call.answer()


@router.message(SupplierProduct.waiting_name)
async def supplier_add_product_name(message: Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(SupplierProduct.waiting_price)
    await message.answer("Введите цену:")


@router.message(SupplierProduct.waiting_price)
async def supplier_add_product_price(message: Message, state: FSMContext):
    try:
        price = float(message.text)
    except ValueError:
        await message.answer("Цена должна быть числом.")
        return
    await state.update_data(price=price)
    await state.set_state(SupplierProduct.waiting_content)
    await message.answer("Введите содержимое:")


@router.message(SupplierProduct.waiting_content)
async def supplier_add_product_content(message: Message, state: FSMContext):
    d = await state.get_data()
    add_product(d["name"], d["price"], message.text, supplier_id=message.from_user.id)
    await state.clear()
    await message.answer("✅ Товар добавлен.", reply_markup=kb_back())


@router.callback_query(F.data == "supplier_my_products")
async def supplier_my_products(call: CallbackQuery):
    if not is_supplier(call.from_user.id):
        return
    products = [p for p in get_products() if p.get("supplier_id") == call.from_user.id]
    if not products:
        await call.message.edit_text("У вас нет товаров.", reply_markup=kb_back())
        await call.answer()
        return
    lines = [f"{p['id']}: {p['name']} - {p['price']}₽" for p in products]
    await call.message.edit_text("📦 Ваши товары:\n" + "\n".join(lines), reply_markup=kb_back())
    await call.answer()


@router.callback_query(F.data == "supplier_my_transactions")
async def supplier_my_transactions(call: CallbackQuery):
    if not is_supplier(call.from_user.id):
        return
    txs = get_transactions(supplier_id=call.from_user.id)
    if not txs:
        await call.message.edit_text("Сделок нет.", reply_markup=kb_back())
        await call.answer()
        return
    lines = [f"#{tx['id']} | покупатель: {tx['user_id']} | товар: {tx['product_id']} | сумма: {tx['amount']}" for tx in txs]
    await call.message.edit_text("📊 Ваши сделки:\n" + "\n".join(lines), reply_markup=kb_back())
    await call.answer()


@router.callback_query(F.data == "menu_shop_user")
async def shop_user_menu(call: CallbackQuery):
    products = get_products()
    if not products:
        await call.message.edit_text("🛒 Товаров нет.", reply_markup=kb_shop_user())
        await call.answer()
        return
    rows = [[InlineKeyboardButton(text=f"🛒 {p['name']} - {p['price']}₽", callback_data=f"buy_product:{p['id']}")] for p in products]
    rows.append([InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_subscription")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_main")])
    await call.message.edit_text("🛒 Магазин:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await call.answer()


@router.callback_query(F.data.startswith("buy_product:"))
async def buy_product(call: CallbackQuery):
    pid = int(call.data.split(":")[1])
    product = get_product(pid)
    if not product:
        await call.answer("Товар не найден.", show_alert=True)
        return
    add_transaction(call.from_user.id, pid, product["price"], product.get("supplier_id"))
    await call.message.answer(f"✅ Товар куплен!\n\n{product['content']}")
    await call.answer("✅ Сделка совершена!")


@router.callback_query(F.data == "buy_subscription")
async def buy_subscription(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 7 дней — 99₽", callback_data="sub_buy_7")],
        [InlineKeyboardButton(text="📅 30 дней — 299₽", callback_data="sub_buy_30")],
        [InlineKeyboardButton(text="📅 90 дней — 699₽", callback_data="sub_buy_90")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu_shop_user")],
    ])
    await call.message.edit_text("💳 Выбери план подписки:", reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("sub_buy_"))
async def sub_buy(call: CallbackQuery):
    days_map = {"sub_buy_7": 7, "sub_buy_30": 30, "sub_buy_90": 90}
    days = days_map.get(call.data)
    if not days:
        await call.answer("Неизвестный план.")
        return
    add_subscription(call.from_user.id, days)
    await call.message.answer(f"✅ Подписка активирована на {days} дней!")
    await call.answer("✅ Готово!")


@router.callback_query(F.data == "my_subscription")
async def my_subscription(call: CallbackQuery):
    sub = check_subscription(call.from_user.id)
    if sub:
        text = f"💳 Подписка активна до {sub['expires_at']}"
    else:
        text = "❌ Нет активной подписки."
    await call.message.edit_text(text, reply_markup=kb_shop_user())
    await call.answer()


# ============================================================
# РОЛИ
# ============================================================

@router.callback_query(F.data == "menu_roles")
async def menu_roles(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("⛔ Только для владельцев.", show_alert=True)
        return
    await call.message.edit_text("👥 Управление ролями", reply_markup=kb_roles_menu())
    await call.answer()


@router.callback_query(F.data == "role_assign")
async def role_assign_start(call: CallbackQuery, state: FSMContext):
    if not is_owner(call.from_user.id):
        return
    await state.set_state(RoleManagement.waiting_user_id)
    await call.message.edit_text("Введите ID пользователя (число):")
    await call.answer()


@router.message(RoleManagement.waiting_user_id)
async def role_assign_user(message: Message, state: FSMContext):
    try:
        uid = int(message.text)
    except ValueError:
        await message.answer("Введите корректный ID.")
        return
    await state.update_data(user_id=uid)
    await state.set_state(RoleManagement.waiting_role)
    await message.answer("Выберите роль (owner/admin/supplier/user):")


@router.message(RoleManagement.waiting_role)
async def role_assign_role(message: Message, state: FSMContext):
    role = message.text.strip().lower()
    if role not in ("owner", "admin", "supplier", "user"):
        await message.answer("Неверная роль.")
        return
    d = await state.get_data()
    set_role(d["user_id"], role)
    await state.clear()
    await message.answer(f"✅ Роль для {d['user_id']} изменена на {role}.", reply_markup=kb_roles_menu())


@router.callback_query(F.data == "role_list")
async def role_list(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        return
    users = get_all_users()
    lines = [f"{uid} — {u['role']}" for uid, u in users.items()]
    await call.message.edit_text(
        "📋 Список пользователей:\n\n" + "\n".join(lines),
        reply_markup=kb_roles_menu()
    )
    await call.answer()


# ============================================================
# Запуск
# ============================================================

async def main():
    log.info("Бот запущен…")
    from userbot import main as userbot_main
    await asyncio.gather(
        userbot_main(aiogram_bot=bot),
        dp.start_polling(bot),
    )


if __name__ == "__main__":
    asyncio.run(main())
