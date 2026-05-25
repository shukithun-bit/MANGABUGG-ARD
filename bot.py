import asyncio
import html
import logging
import os
import random
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

from aiogram import Bot, Dispatcher, F, types
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("hencard")

TOKEN = os.getenv("BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "game.db")
DROP_COOLDOWN_MINUTES = int(os.getenv("DROP_COOLDOWN_MINUTES", "120"))
PREMIUM_DROP_COOLDOWN_MINUTES = int(os.getenv("PREMIUM_DROP_COOLDOWN_MINUTES", "60"))
PREMIUM_DAYS = int(os.getenv("PREMIUM_DAYS", "30"))
START_POINTS = int(os.getenv("START_POINTS", "0"))
UNIVERSE_CHANGE_COST = int(os.getenv("UNIVERSE_CHANGE_COST", "500"))
CARD_IMAGE_DIR = Path(os.getenv("CARD_IMAGE_DIR", "images/cards"))
ADMIN_IDS = {int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}

if not TOKEN:
    raise RuntimeError("Не найден BOT_TOKEN. Создай .env и добавь BOT_TOKEN=твой_токен")

bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())

RARITY_CHANCES = {"Common": 58, "Rare": 25, "Epic": 12, "Legendary": 4, "Mythic": 1}
PREMIUM_RARITY_CHANCES = {"Common": 40, "Rare": 32, "Epic": 18, "Legendary": 7, "Mythic": 3}
RARITY_STYLE = {
    "Common": "⚪ Обычная",
    "Rare": "🔵 Редкая",
    "Epic": "🟣 Эпическая",
    "Legendary": "🟡 Легендарная",
    "Mythic": "🔴 Мифическая",
}
DEFAULT_POINTS = {"Common": 10, "Rare": 25, "Epic": 60, "Legendary": 160, "Mythic": 400}
MEDIA_EXT = {"photo": "jpg", "animation": "gif"}
DONATE_SPIN_PACKAGES = {"1": {"stars": 1, "spins": 1}, "10": {"stars": 10, "spins": 12}, "100": {"stars": 100, "spins": 149}}
PREMIUM_STARS_COST = 100
REFERRAL_REWARD_SPINS = int(os.getenv("REFERRAL_REWARD_SPINS", "5"))
SEASON_POINTS_PER_SPIN = int(os.getenv("SEASON_POINTS_PER_SPIN", "100"))
SEASON_TOP_SPIN_REWARDS = {1: 100, 2: 50, 3: 25}

def main_reply_keyboard(user_id: int | None = None) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="Получить карту"), KeyboardButton(text="Мои карты")],
        [KeyboardButton(text="Меню"), KeyboardButton(text="Профиль")],
    ]
    if user_id is not None and can_add_cards(user_id):
        keyboard.append([KeyboardButton(text="Создать карту")])
    if user_id is not None and is_admin(user_id):
        keyboard.append([KeyboardButton(text="Админ")])
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выбери действие...",
    )


menu = main_reply_keyboard()


def e(value) -> str:
    return html.escape(str(value or ""), quote=False)


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row["name"] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def init_db() -> None:
    CARD_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    with closing(db()) as conn:
        c = conn.cursor()
        c.execute("BEGIN")
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                coins INTEGER DEFAULT 0,
                drops INTEGER DEFAULT 0,
                last_drop_at TEXT,
                shards INTEGER DEFAULT 0,
                spins INTEGER DEFAULT 0,
                daily_streak INTEGER DEFAULT 0,
                last_daily_at TEXT,
                active_universe_id INTEGER,
                nickname TEXT,
                role TEXT DEFAULT 'player',
                premium_until TEXT,
                referrer_id INTEGER,
                pending_referrer_id INTEGER
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS universes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                emoji TEXT DEFAULT '🌌',
                description TEXT,
                is_visible INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS card_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                universe_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                anime TEXT NOT NULL,
                rarity TEXT NOT NULL,
                emoji TEXT NOT NULL,
                price INTEGER NOT NULL,
                points INTEGER NOT NULL DEFAULT 10,
                image_path TEXT,
                media_type TEXT DEFAULT 'photo',
                is_limited INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(universe_id) REFERENCES universes(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                universe_id INTEGER NOT NULL,
                template_id INTEGER,
                name TEXT NOT NULL,
                anime TEXT NOT NULL,
                rarity TEXT NOT NULL,
                emoji TEXT NOT NULL,
                price INTEGER NOT NULL,
                points INTEGER NOT NULL DEFAULT 10,
                image_path TEXT,
                media_type TEXT DEFAULT 'photo',
                is_limited INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id),
                FOREIGN KEY(universe_id) REFERENCES universes(id),
                FOREIGN KEY(template_id) REFERENCES card_templates(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS shop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                universe_id INTEGER,
                title TEXT NOT NULL,
                cost INTEGER NOT NULL,
                item_type TEXT NOT NULL,
                amount INTEGER DEFAULT 0,
                rarities TEXT,
                is_active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT NOT NULL,
                FOREIGN KEY(universe_id) REFERENCES universes(id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS seasons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                number INTEGER NOT NULL,
                is_active INTEGER DEFAULT 1,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                end_message TEXT
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS promo_codes (
                code TEXT PRIMARY KEY,
                reward_type TEXT NOT NULL,
                amount INTEGER NOT NULL,
                max_uses INTEGER DEFAULT 1,
                used_count INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                created_by INTEGER,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS promo_redemptions (
                code TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                redeemed_at TEXT NOT NULL,
                PRIMARY KEY(code, user_id)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                username TEXT,
                item_type TEXT NOT NULL,
                item_title TEXT NOT NULL,
                stars INTEGER NOT NULL,
                reward_amount INTEGER DEFAULT 0,
                currency TEXT NOT NULL,
                payload TEXT NOT NULL,
                telegram_payment_charge_id TEXT,
                provider_payment_charge_id TEXT,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS user_universes (
                user_id INTEGER NOT NULL,
                universe_id INTEGER NOT NULL,
                first_selected_at TEXT NOT NULL,
                PRIMARY KEY(user_id, universe_id)
            )
        """)
        for table, columns in {
            "users": {"shards": "INTEGER DEFAULT 0", "spins": "INTEGER DEFAULT 0", "daily_streak": "INTEGER DEFAULT 0", "last_daily_at": "TEXT", "active_universe_id": "INTEGER", "nickname": "TEXT", "role": "TEXT DEFAULT 'player'", "premium_until": "TEXT", "referrer_id": "INTEGER", "pending_referrer_id": "INTEGER"},
            "universes": {"emoji": "TEXT DEFAULT '🌌'"},
            "card_templates": {"universe_id": "INTEGER", "points": "INTEGER DEFAULT 10", "media_type": "TEXT DEFAULT 'photo'", "is_limited": "INTEGER DEFAULT 0"},
            "cards": {"universe_id": "INTEGER", "points": "INTEGER DEFAULT 10", "media_type": "TEXT DEFAULT 'photo'", "is_limited": "INTEGER DEFAULT 0"},
        }.items():
            for col, ddl in columns.items():
                if not column_exists(conn, table, col):
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cards_user_universe ON cards(user_id, universe_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_cards_user_template ON cards(user_id, template_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_templates_universe_active ON card_templates(universe_id, is_active)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_shop_universe_active ON shop_items(universe_id, is_active)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_payments_created ON payments(created_at)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_user_universes_user ON user_universes(user_id)")
        c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('universe_change_cost', ?)", (str(UNIVERSE_CHANGE_COST),))
        conn.commit()


def ensure_user(user: types.User) -> None:
    username = user.username or user.full_name or "Игрок"
    with closing(db()) as conn:
        conn.execute("BEGIN")
        conn.execute("INSERT OR IGNORE INTO users (id, username, coins, nickname) VALUES (?, ?, ?, ?)", (user.id, username, START_POINTS, username))
        conn.execute("UPDATE users SET username=? WHERE id=?", (username, user.id))
        conn.execute("UPDATE users SET nickname=COALESCE(nickname, username) WHERE id=?", (user.id,))
        conn.commit()


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def get_setting(key: str, default: str) -> str:
    with closing(db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def universe_change_cost() -> int:
    try:
        return int(get_setting("universe_change_cost", str(UNIVERSE_CHANGE_COST)))
    except ValueError:
        return UNIVERSE_CHANGE_COST


def user_role(user_id: int) -> str:
    if is_admin(user_id):
        return "creator"
    with closing(db()) as conn:
        row = conn.execute("SELECT role FROM users WHERE id=?", (user_id,)).fetchone()
    return (row["role"] if row else "player") or "player"


def can_add_cards(user_id: int) -> bool:
    return is_admin(user_id) or user_role(user_id) == "moderator"


def is_premium_row(row) -> bool:
    if not row or "premium_until" not in row.keys() or not row["premium_until"]:
        return False
    try:
        return datetime.fromisoformat(row["premium_until"]) > datetime.utcnow()
    except ValueError:
        return False


def is_premium_user(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT premium_until FROM users WHERE id=?", (user_id,)).fetchone()
    return is_premium_row(row)


def drop_cooldown_minutes(conn: sqlite3.Connection, user_id: int) -> int:
    return PREMIUM_DROP_COOLDOWN_MINUTES if is_premium_user(conn, user_id) else DROP_COOLDOWN_MINUTES


def rarity_chances_for_user(conn: sqlite3.Connection, user_id: int | None = None) -> dict[str, int]:
    return PREMIUM_RARITY_CHANCES if user_id is not None and is_premium_user(conn, user_id) else RARITY_CHANCES


def admin_guard(obj) -> bool:
    return bool(obj.from_user and is_admin(obj.from_user.id))


def card_admin_guard(obj) -> bool:
    return bool(obj.from_user and can_add_cards(obj.from_user.id))


def safe_filename(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁ_-]+", "_", text.strip()).strip("_")
    return slug[:40] or "card"


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛒 Магазин", callback_data="menu:shop"), InlineKeyboardButton(text="🎁 Бонус", callback_data="menu:daily")],
        [InlineKeyboardButton(text="🏆 Топ", callback_data="menu:top")],
        [InlineKeyboardButton(text="ℹ️ Помощь", callback_data="menu:help")],
    ])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
    ])


def profile_keyboard(is_own_profile: bool = True) -> InlineKeyboardMarkup:
    rows = []
    if is_own_profile:
        rows.append([InlineKeyboardButton(text="🔗 Пригласить друга", callback_data="profile:ref")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def cancel_fsm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❌ Отмена", callback_data="fsm:cancel")],
    ])


def admin_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить карту", callback_data="admin:add_card")],
        [InlineKeyboardButton(text="🛒 Товары магазина", callback_data="admin:shop")],
        [InlineKeyboardButton(text="🎁 Подарить крутки", callback_data="admin:gift_spins")],
        [InlineKeyboardButton(text="🌌 Вселенные", callback_data="admin:universes")],
        [InlineKeyboardButton(text="📋 Список карт", callback_data="admin:list_cards")],
        [InlineKeyboardButton(text="⚙️ Настройки цен", callback_data="admin:settings")],
        [InlineKeyboardButton(text="🎟 Промокоды", callback_data="admin:promos")],
        [InlineKeyboardButton(text="👮 Модераторы", callback_data="admin:moderators")],
        [InlineKeyboardButton(text="🏁 Сезоны", callback_data="admin:seasons")],
        [InlineKeyboardButton(text="⭐ Покупки", callback_data="admin:payments")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin:stats")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
    ])


def rarity_keyboard(prefix="rarity") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=v, callback_data=f"{prefix}:{k}")] for k, v in RARITY_STYLE.items()])


def universe_keyboard(prefix: str, include_back=True, only_visible=True) -> InlineKeyboardMarkup:
    with closing(db()) as conn:
        if only_visible:
            rows = conn.execute("SELECT * FROM universes WHERE is_visible=1 ORDER BY name").fetchall()
        else:
            rows = conn.execute("SELECT * FROM universes ORDER BY is_visible DESC, name").fetchall()
    buttons = [[InlineKeyboardButton(text=f"{row['emoji'] or '🌌'} {row['name']}", callback_data=f"{prefix}:{row['id']}")] for row in rows]
    if include_back:
        buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def admin_universes_keyboard() -> InlineKeyboardMarkup:
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM universes ORDER BY is_visible DESC, name").fetchall()
    kb = [[InlineKeyboardButton(text="➕ Создать вселенную", callback_data="admin:universe_create")]]
    for row in rows:
        status = "✅" if row["is_visible"] else "🔒"
        kb.append([InlineKeyboardButton(text=f"{status} {row['emoji'] or '🌌'} {row['name']}", callback_data=f"admin:universe:{row['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def collection_filter_keyboard(user_id: int, universe_id: int | None = None) -> InlineKeyboardMarkup:
    with closing(db()) as conn:
        universe_id = universe_id or active_universe_id(conn, user_id)
        totals = {r["rarity"]: r["total"] for r in conn.execute("SELECT rarity, COUNT(*) AS total FROM card_templates WHERE is_active=1 AND universe_id=? GROUP BY rarity", (universe_id,)).fetchall()} if universe_id else {}
        owned = {r["rarity"]: r["owned"] for r in conn.execute("SELECT rarity, COUNT(DISTINCT template_id) AS owned FROM cards WHERE user_id=? AND universe_id=? GROUP BY rarity", (user_id, universe_id)).fetchall()} if universe_id else {}
    rows = [[InlineKeyboardButton(text="🌈 Все карты", callback_data=f"collection:{universe_id}:All")]]
    for rarity, title in RARITY_STYLE.items():
        rows.append([InlineKeyboardButton(text=f"{title} — {owned.get(rarity, 0)}/{totals.get(rarity, 0)}", callback_data=f"collection:{universe_id}:{rarity}")])
    rows.append([InlineKeyboardButton(text="💎 Раскол дублей", callback_data="menu:dupes")])
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def shop_keyboard(user_id: int) -> InlineKeyboardMarkup:
    with closing(db()) as conn:
        universe_id = active_universe_id(conn, user_id)
        rows = conn.execute("SELECT * FROM shop_items WHERE is_active=1 AND (universe_id IS NULL OR universe_id=?) ORDER BY cost, id", (universe_id,)).fetchall()
    kb = [[InlineKeyboardButton(text=f"{row['title']} — {row['cost']} 💎", callback_data=f"shop:buy:{row['id']}")] for row in rows]
    kb.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def donate_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 1 крутка — 1 ⭐", callback_data="donate:spins:1")],
        [InlineKeyboardButton(text="🎟 12 круток — 10 ⭐", callback_data="donate:spins:10")],
        [InlineKeyboardButton(text="🎟 149 круток — 100 ⭐", callback_data="donate:spins:100")],
        [InlineKeyboardButton(text=f"💫 Премиум на {PREMIUM_DAYS} дн. — {PREMIUM_STARS_COST} ⭐", callback_data="donate:premium")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
    ])


def admin_shop_keyboard() -> InlineKeyboardMarkup:
    with closing(db()) as conn:
        rows = conn.execute("SELECT si.*, u.name AS universe_name FROM shop_items si LEFT JOIN universes u ON u.id=si.universe_id ORDER BY si.is_active DESC, si.cost, si.id LIMIT 40").fetchall()
    kb = [[InlineKeyboardButton(text="➕ Добавить товар", callback_data="admin:shop_add")]]
    for row in rows:
        status = "✅" if row["is_active"] else "🔒"
        scope = row["universe_name"] or "Все вселенные"
        kb.append([InlineKeyboardButton(text=f"{status} {row['title']} • {row['cost']} 💎 • {scope}", callback_data=f"admin:shop_item:{row['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def shop_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎟 Крутки", callback_data="shoptype:spins")],
        [InlineKeyboardButton(text="🎴 Карта выбранной редкости", callback_data="shoptype:card")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:shop")],
    ])


class AddCard(StatesGroup):
    waiting_universe = State()
    waiting_media = State()
    waiting_name = State()
    waiting_rarity = State()
    waiting_price = State()
    waiting_points = State()
    waiting_limited = State()
    waiting_confirm = State()


class UniverseAdmin(StatesGroup):
    waiting_name = State()
    waiting_emoji = State()
    waiting_description = State()


class AddShopItem(StatesGroup):
    waiting_universe = State()
    waiting_type = State()
    waiting_title = State()
    waiting_cost = State()
    waiting_amount = State()
    waiting_rarity = State()


class GiftSpins(StatesGroup):
    waiting_user = State()
    waiting_amount = State()


class NicknameChange(StatesGroup):
    waiting_nick = State()


class SettingsAdmin(StatesGroup):
    waiting_universe_change_cost = State()


class ModeratorAdmin(StatesGroup):
    waiting_user = State()


class SeasonAdmin(StatesGroup):
    waiting_start_number = State()
    waiting_end_message = State()




async def remove_old_reply_keyboard(message: types.Message) -> None:
    """Убирает старую нижнюю клавиатуру Telegram, если она осталась у пользователя после прошлой версии."""
    try:
        service_msg = await message.answer("⌨️ Обновляю меню...", reply_markup=ReplyKeyboardRemove())
        try:
            await service_msg.delete()
        except TelegramBadRequest:
            pass
    except TelegramBadRequest:
        pass

async def edit_or_answer(target, text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
    if isinstance(target, types.CallbackQuery):
        try:
            await target.message.edit_text(text, reply_markup=reply_markup, parse_mode="HTML")
            return
        except TelegramBadRequest:
            try:
                await target.message.delete()
            except TelegramBadRequest:
                pass
            await target.message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
            return
    await target.answer(text, reply_markup=reply_markup or menu, parse_mode="HTML")


def active_universe_id(conn: sqlite3.Connection, user_id: int) -> int | None:
    row = conn.execute("SELECT active_universe_id FROM users WHERE id=?", (user_id,)).fetchone()
    return row["active_universe_id"] if row else None


def get_active_universe(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    uid = active_universe_id(conn, user_id)
    if not uid:
        return None
    return conn.execute("SELECT * FROM universes WHERE id=?", (uid,)).fetchone()


def remember_user_universe(conn: sqlite3.Connection, user_id: int, universe_id: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO user_universes (user_id, universe_id, first_selected_at) VALUES (?, ?, ?)",
        (user_id, universe_id, datetime.utcnow().isoformat()),
    )


async def require_universe(target, user: types.User) -> int | None:
    ensure_user(user)
    with closing(db()) as conn:
        uid = active_universe_id(conn, user.id)
        exists = conn.execute("SELECT 1 FROM universes WHERE id=?", (uid,)).fetchone() if uid else None
        visible_count = conn.execute("SELECT COUNT(*) AS total FROM universes WHERE is_visible=1").fetchone()["total"]
    if uid and exists:
        return uid
    if visible_count == 0:
        await edit_or_answer(target, "🌌 Пока нет открытых вселенных. Создатель должен добавить вселенную через админ-панель.", main_menu_keyboard())
    else:
        await edit_or_answer(target, "🌌 <b>Выбери вселенную</b>\n\nПосле выбора тебе будут выпадать карты только из неё.", universe_keyboard("universe:select", include_back=False))
    return None


def card_points(card) -> int:
    return int(card["points"] if "points" in card.keys() and card["points"] is not None else DEFAULT_POINTS.get(card["rarity"], 5))


def format_card(card, index: int | None = None, copies: int | None = None) -> str:
    prefix = f"{index}. " if index is not None else ""
    copies_text = f" ×{copies}" if copies and copies > 1 else ""
    rarity = RARITY_STYLE.get(card["rarity"], card["rarity"])
    limited = " • лимитка" if "is_limited" in card.keys() and card["is_limited"] else ""
    return f"{prefix}{e(card['emoji'])} <b>{e(card['name'])}</b>{copies_text} — {e(rarity)}{limited}"


def format_card_reward(points: int, total_points: int, shards: int, total_shards: int, spins: int | None = None) -> str:
    lines = [
        f"⭐ Получено очков: <b>+{points}</b>",
        f"💎 Получено осколков: <b>+{shards}</b>",
        "",
        f"⭐ Всего очков: <b>{total_points}</b>",
        f"💎 Всего осколков: <b>{total_shards}</b>",
    ]
    if spins is not None:
        lines.append(f"🎟 Крутки: <b>{spins}</b>")
    return "\n".join(lines)


def pick_rarity(conn: sqlite3.Connection, user_id: int | None = None) -> str:
    chances = rarity_chances_for_user(conn, user_id)
    return random.choices(list(chances), weights=list(chances.values()), k=1)[0]


def pick_card_template(conn: sqlite3.Connection, universe_id: int, rarities: list[str] | None = None, user_id: int | None = None) -> sqlite3.Row:
    if rarities:
        ph = ",".join("?" for _ in rarities)
        pool = conn.execute(f"SELECT * FROM card_templates WHERE is_active=1 AND universe_id=? AND rarity IN ({ph})", [universe_id, *rarities]).fetchall()
    else:
        rarity = pick_rarity(conn, user_id)
        pool = conn.execute("SELECT * FROM card_templates WHERE is_active=1 AND universe_id=? AND rarity=?", (universe_id, rarity)).fetchall()
        if not pool:
            pool = conn.execute("SELECT * FROM card_templates WHERE is_active=1 AND universe_id=?", (universe_id,)).fetchall()
    if not pool:
        raise RuntimeError("В выбранной вселенной нет активных карт. Добавь карты через админ-панель.")
    weights = [max(1, RARITY_CHANCES.get(row["rarity"], 1)) for row in pool]
    return random.choices(pool, weights=weights, k=1)[0]


def grant_card(conn: sqlite3.Connection, user_id: int, card: sqlite3.Row, now: datetime) -> tuple[int, int]:
    points = card_points(card)
    shards = int(card["price"] if "price" in card.keys() and card["price"] is not None else 0)
    conn.execute("""
        INSERT INTO cards (user_id, universe_id, template_id, name, anime, rarity, emoji, price, points, image_path, media_type, is_limited, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (user_id, card["universe_id"], card["id"], card["name"], card["anime"], card["rarity"], card["emoji"], card["price"], points, card["image_path"], card["media_type"], card["is_limited"] if "is_limited" in card.keys() else 0, now.isoformat()))
    conn.execute("UPDATE users SET coins=coins+?, shards=shards+? WHERE id=?", (points, shards, user_id))
    return points, shards


def seconds_to_text(seconds: int) -> str:
    minutes, sec = divmod(max(seconds, 0), 60)
    return f"{minutes} мин. {sec} сек."


def home_text(user: types.User) -> str:
    with closing(db()) as conn:
        universe = get_active_universe(conn, user.id)
        row = conn.execute("SELECT * FROM users WHERE id=?", (user.id,)).fetchone()
    current = f"{universe['emoji'] or '🌌'} {universe['name']}" if universe else "не выбрана"
    nick = display_name(row)
    return (
        "✨ <b>HenCard</b>\n\n"
        f"Игрок: <b>{e(nick)}</b>\n"
        f"🌌 Вселенная: <b>{e(current)}</b>\n\n"
        "Собирай карты, получай очки за каждую карту, раскалывай дубли и поднимайся в рейтинге."
    )


def display_name(row) -> str:
    if not row:
        return "Игрок"
    return row["nickname"] or row["username"] or f"ID {row['id']}"


def resolve_user_identifier(text: str) -> sqlite3.Row | None:
    raw = (text or "").strip()
    if not raw:
        return None
    with closing(db()) as conn:
        if raw.isdigit():
            row = conn.execute("SELECT * FROM users WHERE id=?", (int(raw),)).fetchone()
            if row: return row
        q = raw[1:] if raw.startswith("@") else raw
        row = conn.execute("SELECT * FROM users WHERE lower(username)=lower(?) OR lower(nickname)=lower(?)", (q, q)).fetchone()
        if row: return row
        like = f"%{q}%"
        return conn.execute("SELECT * FROM users WHERE lower(nickname) LIKE lower(?) OR lower(username) LIKE lower(?) ORDER BY id LIMIT 1", (like, like)).fetchone()


def register_pending_referral(user_id: int, referrer_id: int) -> bool:
    if user_id == referrer_id:
        return False
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute("SELECT referrer_id, pending_referrer_id FROM users WHERE id=?", (user_id,)).fetchone()
        referrer = conn.execute("SELECT id FROM users WHERE id=?", (referrer_id,)).fetchone()
        if not user or not referrer or user["referrer_id"] is not None or user["pending_referrer_id"] is not None:
            conn.rollback()
            return False
        conn.execute("UPDATE users SET pending_referrer_id=? WHERE id=?", (referrer_id, user_id))
        conn.commit()
        return True


def complete_referral_if_ready(conn: sqlite3.Connection, user_id: int) -> bool:
    user = conn.execute("SELECT pending_referrer_id, referrer_id, active_universe_id FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or not user["pending_referrer_id"] or user["referrer_id"] is not None or not user["active_universe_id"]:
        return False
    cards_count = conn.execute("SELECT COUNT(*) AS total FROM cards WHERE user_id=?", (user_id,)).fetchone()["total"]
    if cards_count < 5:
        return False
    referrer_id = user["pending_referrer_id"]
    conn.execute("UPDATE users SET referrer_id=?, pending_referrer_id=NULL, spins=spins+? WHERE id=?", (referrer_id, REFERRAL_REWARD_SPINS, user_id))
    conn.execute("UPDATE users SET spins=spins+? WHERE id=?", (REFERRAL_REWARD_SPINS, referrer_id))
    return True


async def send_card_result(target, caption: str, card) -> None:
    path = card["image_path"] if "image_path" in card.keys() else None
    media_type = card["media_type"] if "media_type" in card.keys() else "photo"
    chat_id = target.message.chat.id if isinstance(target, types.CallbackQuery) else target.chat.id
    if isinstance(target, types.CallbackQuery):
        try:
            await target.message.delete()
        except TelegramBadRequest:
            pass
    if path and Path(path).exists() and media_type == "animation":
        await bot.send_animation(chat_id, types.FSInputFile(path), caption=caption, parse_mode="HTML", reply_markup=main_menu_keyboard())
    elif path and Path(path).exists():
        await bot.send_photo(chat_id, types.FSInputFile(path), caption=caption, parse_mode="HTML", reply_markup=main_menu_keyboard())
    else:
        await (target.message.answer if isinstance(target, types.CallbackQuery) else target.answer)(caption, reply_markup=main_menu_keyboard(), parse_mode="HTML")


async def send_card_preview(chat_id: int, data: dict, caption_prefix="👀 <b>Предпросмотр карты</b>\n\n") -> None:
    row = {"name": data["name"], "anime": data["anime"], "rarity": data["rarity"], "emoji": data["emoji"], "price": int(data["price"]), "points": int(data["points"])}
    caption = caption_prefix + f"🌌 Вселенная: <b>{e(data.get('universe_name'))}</b>\n\n" + format_card(row)
    path = data.get("image_path")
    media_type = data.get("media_type", "photo")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Опубликовать", callback_data="card:publish")],
        [InlineKeyboardButton(text="✏️ Заново", callback_data="card:restart")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="card:cancel")],
    ])
    if path and Path(path).exists() and media_type == "animation":
        await bot.send_animation(chat_id, types.FSInputFile(path), caption=caption, parse_mode="HTML", reply_markup=kb)
    elif path and Path(path).exists():
        await bot.send_photo(chat_id, types.FSInputFile(path), caption=caption, parse_mode="HTML", reply_markup=kb)
    else:
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=kb)


@dp.message(CommandStart())
async def start(message: types.Message):
    ensure_user(message.from_user)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].startswith("ref_") and parts[1][4:].isdigit():
        register_pending_referral(message.from_user.id, int(parts[1][4:]))
    await remove_old_reply_keyboard(message)
    with closing(db()) as conn:
        has_universe = active_universe_id(conn, message.from_user.id) is not None
        visible = conn.execute("SELECT COUNT(*) AS total FROM universes WHERE is_visible=1").fetchone()["total"]
    if not has_universe and visible:
        await message.answer("⌨️ Нижнее меню обновлено.", reply_markup=main_reply_keyboard(message.from_user.id))
        await message.answer("🌌 <b>Добро пожаловать в HenCard!</b>\n\nВыбери вселенную. После этого тебе будут выпадать карты только из неё.", reply_markup=universe_keyboard("universe:select", include_back=False), parse_mode="HTML")
    else:
        await message.answer(home_text(message.from_user), reply_markup=main_reply_keyboard(message.from_user.id), parse_mode="HTML")


@dp.message(Command("menu"))
@dp.message(F.text == "Меню")
async def open_menu(message: types.Message):
    ensure_user(message.from_user)
    with closing(db()) as conn:
        has_universe = active_universe_id(conn, message.from_user.id) is not None
        visible = conn.execute("SELECT COUNT(*) AS total FROM universes WHERE is_visible=1").fetchone()["total"]
    if not has_universe and visible:
        await message.answer("🌌 <b>Сначала выбери вселенную</b>", reply_markup=universe_keyboard("universe:select", include_back=False), parse_mode="HTML")
    else:
        await message.answer(home_text(message.from_user), reply_markup=main_menu_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "menu:home")
async def menu_home(query: types.CallbackQuery):
    ensure_user(query.from_user)
    await edit_or_answer(query, home_text(query.from_user), main_menu_keyboard())
    await query.answer()


@dp.callback_query(F.data.startswith("universe:select:"))
async def select_universe(query: types.CallbackQuery):
    ensure_user(query.from_user)
    uid = int(query.data.rsplit(":", 1)[1])
    with closing(db()) as conn:
        universe = conn.execute("SELECT * FROM universes WHERE id=? AND is_visible=1", (uid,)).fetchone()
        if not universe:
            await query.answer("Вселенная закрыта или не найдена", show_alert=True)
            return
        conn.execute("UPDATE users SET active_universe_id=? WHERE id=?", (uid, query.from_user.id))
        remember_user_universe(conn, query.from_user.id, uid)
    await edit_or_answer(query, f"✅ Вселенная выбрана: <b>{e(universe['emoji'])} {e(universe['name'])}</b>", main_menu_keyboard())
    await query.message.answer("⌨️ Нижнее меню готово.", reply_markup=main_reply_keyboard(query.from_user.id))
    await query.answer()


HELP_TEXT = (
    "<b>Помощь HenCard</b>\n\n"
    "🎴 <b>Получить карту</b> — главная кнопка игры. Бесплатную карту можно получить по таймеру. Если есть крутки, они тратятся без ожидания.\n"
    "🌌 <b>Вселенная</b> — набор карт, из которого тебе выпадают персонажи. При первом входе бот попросит выбрать вселенную. Если захочешь сменить её позже, используй команду /universe. Карты из прошлых вселенных сохраняются.\n"
    "🎒 <b>Коллекция</b> — показывает прогресс по редкостям: обычные, редкие, эпические, легендарные и мифические. Нажми на редкость, чтобы листать свои карты с картинками.\n"
    "🎁 <b>Бонус</b> — ежедневная награда крутками. Чем дольше забираешь подряд, тем приятнее серия.\n"
    "💎 <b>Раскол дублей</b> — лишние копии карт превращаются в осколки. Осколки нужны для покупок в магазине.\n"
    "🛒 <b>Магазин</b> — доступен в любое время. Там есть товары за осколки.\n"
    "⭐ <b>Поддержать проект</b> — покупка круток и премиума за Telegram Stars. Премиум уменьшает ожидание карты и улучшает шанс редких карт.\n"
    "🔗 <b>Рефералка</b> — друг должен перейти по твоей ссылке, выбрать вселенную и получить 5 карт. Только после этого приглашение засчитывается, а вы оба получаете крутки.\n"
    "🏆 <b>Топ</b> — рейтинг игроков по очкам и отдельный рейтинг по приглашённым друзьям.\n"
    "👤 <b>Профиль</b> — твои очки, крутки, премиум, коллекция и статистика приглашений.\n"
)


def help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⭐ Поддержать проект", callback_data="shop:donate")],
        [InlineKeyboardButton(text="📣 Наш Канал", url="https://t.me/hen_card")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
    ])


@dp.message(Command("help"))
@dp.message(F.text == "ℹ️ Помощь")
async def help_message(message: types.Message):
    ensure_user(message.from_user)
    admin = "\nАдмин-команды: /admin, /addcard" if is_admin(message.from_user.id) else ""
    await message.answer(HELP_TEXT + admin, parse_mode="HTML", reply_markup=help_keyboard())


@dp.callback_query(F.data == "menu:help")
async def help_button(query: types.CallbackQuery):
    admin = "\nАдмин-команды: /admin, /addcard" if is_admin(query.from_user.id) else ""
    await edit_or_answer(query, HELP_TEXT + admin, help_keyboard())
    await query.answer()




async def referral_text(user_id: int) -> str:
    with closing(db()) as conn:
        referrals = conn.execute("SELECT COUNT(*) AS total FROM users WHERE referrer_id=?", (user_id,)).fetchone()["total"]
        pending = conn.execute("SELECT COUNT(*) AS total FROM users WHERE pending_referrer_id=?", (user_id,)).fetchone()["total"]
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{user_id}"
    return f"🔗 <b>Твоя реферальная ссылка</b>\n{link}\n\nЗасчитано друзей: <b>{referrals}</b>\nВ процессе: <b>{pending}</b>\n\nДруг должен перейти по ссылке, выбрать вселенную и получить <b>5 карт</b>. После этого вы оба получите <b>{REFERRAL_REWARD_SPINS}</b> круток."


@dp.message(Command("ref"))
async def referral_link(message: types.Message):
    ensure_user(message.from_user)
    await message.answer(await referral_text(message.from_user.id), parse_mode="HTML", reply_markup=back_to_menu_keyboard())


@dp.callback_query(F.data == "profile:ref")
async def referral_button(query: types.CallbackQuery):
    ensure_user(query.from_user)
    await edit_or_answer(query, await referral_text(query.from_user.id), back_to_menu_keyboard())
    await query.answer()


@dp.message(Command("promo"))
async def redeem_promo(message: types.Message):
    ensure_user(message.from_user)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Использование: /promo КОД", reply_markup=main_menu_keyboard())
        return
    code = parts[1].strip().upper()
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        promo = conn.execute("SELECT * FROM promo_codes WHERE code=? AND is_active=1", (code,)).fetchone()
        if not promo:
            conn.rollback(); await message.answer("Промокод не найден или выключен.", reply_markup=main_menu_keyboard()); return
        if promo["used_count"] >= promo["max_uses"]:
            conn.rollback(); await message.answer("У промокода закончились активации.", reply_markup=main_menu_keyboard()); return
        used = conn.execute("SELECT 1 FROM promo_redemptions WHERE code=? AND user_id=?", (code, message.from_user.id)).fetchone()
        if used:
            conn.rollback(); await message.answer("Ты уже активировал этот промокод.", reply_markup=main_menu_keyboard()); return
        if promo["reward_type"] == "spins":
            conn.execute("UPDATE users SET spins=spins+? WHERE id=?", (promo["amount"], message.from_user.id))
            reward_text = f"🎟 Крутки: <b>+{promo['amount']}</b>"
        elif promo["reward_type"] == "shards":
            conn.execute("UPDATE users SET shards=shards+? WHERE id=?", (promo["amount"], message.from_user.id))
            reward_text = f"💎 Осколки: <b>+{promo['amount']}</b>"
        else:
            conn.rollback(); await message.answer("У промокода неизвестный тип награды.", reply_markup=main_menu_keyboard()); return
        conn.execute("INSERT INTO promo_redemptions (code, user_id, redeemed_at) VALUES (?, ?, ?)", (code, message.from_user.id, datetime.utcnow().isoformat()))
        conn.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE code=?", (code,))
        conn.commit()
    await message.answer(f"✅ Промокод активирован!\n{reward_text}", parse_mode="HTML", reply_markup=main_menu_keyboard())


@dp.message(Command("addpromo"))
async def add_promo(message: types.Message):
    ensure_user(message.from_user)
    if not admin_guard(message):
        await message.answer("Нет доступа.")
        return
    parts = (message.text or "").split()
    if len(parts) != 5 or parts[2] not in {"spins", "shards"} or not parts[3].isdigit() or not parts[4].isdigit():
        await message.answer("Использование: /addpromo CODE spins|shards AMOUNT USES")
        return
    code = parts[1].upper()
    with closing(db()) as conn:
        conn.execute("INSERT OR REPLACE INTO promo_codes (code, reward_type, amount, max_uses, used_count, is_active, created_by, created_at) VALUES (?, ?, ?, ?, 0, 1, ?, ?)", (code, parts[2], int(parts[3]), int(parts[4]), message.from_user.id, datetime.utcnow().isoformat()))
    await message.answer(f"✅ Промокод <b>{e(code)}</b> создан.", parse_mode="HTML", reply_markup=admin_menu_keyboard())


@dp.callback_query(F.data == "admin:promos")
async def admin_promos(query: types.CallbackQuery):
    if not admin_guard(query): await query.answer("Нет доступа", show_alert=True); return
    with closing(db()) as conn:
        rows = conn.execute("SELECT * FROM promo_codes ORDER BY created_at DESC LIMIT 20").fetchall()
    lines = ["🎟 <b>Промокоды</b>", "Создание: <code>/addpromo CODE spins 10 100</code>", "Активация игроком: <code>/promo CODE</code>"]
    if rows:
        lines.append("")
        lines.extend(f"<b>{e(r['code'])}</b> — {e(r['reward_type'])} {r['amount']} • {r['used_count']}/{r['max_uses']}" for r in rows)
    await edit_or_answer(query, "\n".join(lines), admin_menu_keyboard())
    await query.answer()


@dp.message(Command("admin"))
@dp.message(F.text == "Админ")
async def admin_panel(message: types.Message):
    ensure_user(message.from_user)
    if not admin_guard(message):
        await message.answer("⛔ У тебя нет доступа к админ-панели.")
        return
    await message.answer("🛠 <b>Админ-панель HenCard</b>", parse_mode="HTML", reply_markup=admin_menu_keyboard())


@dp.callback_query(F.data == "admin:back")
async def admin_back(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    await edit_or_answer(query, "🛠 <b>Админ-панель HenCard</b>", admin_menu_keyboard())
    await query.answer()


@dp.callback_query(F.data == "admin:stats")
async def admin_stats(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    with closing(db()) as conn:
        users = conn.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
        universes = conn.execute("SELECT COUNT(*) AS total FROM universes").fetchone()["total"]
        templates = conn.execute("SELECT COUNT(*) AS total FROM card_templates WHERE is_active=1").fetchone()["total"]
        cards = conn.execute("SELECT COUNT(*) AS total FROM cards").fetchone()["total"]
        items = conn.execute("SELECT COUNT(*) AS total FROM shop_items WHERE is_active=1").fetchone()["total"]
    await edit_or_answer(query, f"📊 <b>Статистика</b>\n\n👥 Игроков: <b>{users}</b>\n🌌 Вселенных: <b>{universes}</b>\n🃏 Активных карт: <b>{templates}</b>\n🎴 Выдано карт: <b>{cards}</b>\n🛒 Товаров: <b>{items}</b>", admin_menu_keyboard())
    await query.answer()


@dp.callback_query(F.data == "admin:payments")
async def admin_payments(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    with closing(db()) as conn:
        total = conn.execute("SELECT COUNT(*) AS total, COALESCE(SUM(stars),0) AS stars FROM payments").fetchone()
        rows = conn.execute("""
            SELECT p.*, u.nickname, u.username AS current_username
            FROM payments p
            LEFT JOIN users u ON u.id = p.user_id
            ORDER BY p.created_at DESC
            LIMIT 15
        """).fetchall()
    lines = [
        "⭐ <b>Покупки за Stars</b>",
        f"Всего покупок: <b>{total['total']}</b>",
        f"Всего Stars: <b>{total['stars']}</b>",
    ]
    if rows:
        lines.append("")
        for row in rows:
            created = row["created_at"][:16].replace("T", " ")
            name = row["nickname"] or row["current_username"] or row["username"] or str(row["user_id"])
            charge_id = row["telegram_payment_charge_id"] or "нет id"
            lines.append(
                f"{created} • <b>{e(name)}</b> • {e(row['item_title'])} • <b>{row['stars']} ⭐</b>\n"
                f"<code>{e(charge_id)}</code>"
            )
    else:
        lines.append("\nПокупок пока нет.")
    await edit_or_answer(query, "\n".join(lines), admin_menu_keyboard())
    await query.answer()


@dp.callback_query(F.data == "admin:universes")
async def admin_universes(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    await edit_or_answer(query, "🌌 <b>Управление вселенными</b>\n\n✅ — видна игрокам\n🔒 — закрыта для выбора", admin_universes_keyboard())
    await query.answer()


@dp.callback_query(F.data == "admin:universe_create")
async def admin_universe_create(query: types.CallbackQuery, state: FSMContext):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(UniverseAdmin.waiting_name)
    await edit_or_answer(query, "➕ <b>Создание вселенной</b>\n\nВведи название вселенной.\n\nОтмена: /cancel", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="fsm:cancel")]]))
    await query.answer()


@dp.message(UniverseAdmin.waiting_name)
async def admin_universe_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("Название слишком короткое.")
        return
    await state.update_data(name=name)
    await state.set_state(UniverseAdmin.waiting_emoji)
    await message.answer("Отправь эмодзи вселенной. Например: 🌀")


@dp.message(UniverseAdmin.waiting_emoji)
async def admin_universe_emoji(message: types.Message, state: FSMContext):
    await state.update_data(emoji=(message.text or "🌌").strip()[:4])
    await state.set_state(UniverseAdmin.waiting_description)
    await message.answer("Введи описание вселенной. Можно написать '-' если описание не нужно.")


@dp.message(UniverseAdmin.waiting_description)
async def admin_universe_description(message: types.Message, state: FSMContext):
    data = await state.get_data()
    desc = "" if (message.text or "").strip() == "-" else (message.text or "").strip()
    try:
        with closing(db()) as conn:
            conn.execute("INSERT INTO universes (name, emoji, description, is_visible, created_by, created_at) VALUES (?, ?, ?, 1, ?, ?)", (data["name"], data["emoji"], desc, message.from_user.id, datetime.utcnow().isoformat()))
    except sqlite3.IntegrityError:
        await message.answer("Такая вселенная уже есть.", reply_markup=admin_universes_keyboard())
        await state.clear()
        return
    await state.clear()
    await message.answer(f"✅ Вселенная <b>{e(data['emoji'])} {e(data['name'])}</b> создана и открыта для выбора.", parse_mode="HTML", reply_markup=admin_universes_keyboard())


@dp.callback_query(F.data.startswith("admin:universe:"))
async def admin_universe_open(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    uid = int(query.data.rsplit(":", 1)[1])
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM universes WHERE id=?", (uid,)).fetchone()
        cards = conn.execute("SELECT COUNT(*) AS total FROM card_templates WHERE universe_id=?", (uid,)).fetchone()["total"] if row else 0
    if not row:
        await query.answer("Вселенная не найдена", show_alert=True)
        return
    toggle = "🔒 Закрыть для выбора" if row["is_visible"] else "✅ Открыть для выбора"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle, callback_data=f"admin:universe_toggle:{uid}")],
        [InlineKeyboardButton(text="🗑 Удалить вселенную", callback_data=f"admin:universe_delete_confirm:{uid}")],
        [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin:universes")],
    ])
    await edit_or_answer(query, f"{e(row['emoji'])} <b>{e(row['name'])}</b>\n\n{e(row['description'])}\n\nСтатус: <b>{'видна' if row['is_visible'] else 'закрыта'}</b>\nКарт: <b>{cards}</b>", kb)
    await query.answer()


@dp.callback_query(F.data.startswith("admin:universe_toggle:"))
async def admin_universe_toggle(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    uid = int(query.data.rsplit(":", 1)[1])
    with closing(db()) as conn:
        row = conn.execute("SELECT is_visible FROM universes WHERE id=?", (uid,)).fetchone()
        if row:
            conn.execute("UPDATE universes SET is_visible=? WHERE id=?", (0 if row["is_visible"] else 1, uid))
    await edit_or_answer(query, "✅ Статус вселенной изменён.", admin_universes_keyboard())
    await query.answer()


@dp.callback_query(F.data == "admin:add_card")
async def admin_add_from_button(query: types.CallbackQuery, state: FSMContext):
    if not card_admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddCard.waiting_universe)
    await edit_or_answer(query, "➕ <b>Добавление карты</b>\n\nВыбери вселенную, куда добавить карту.", universe_keyboard("addcard:universe", include_back=True, only_visible=False))
    await query.answer()


@dp.message(Command("addcard"))
@dp.message(F.text == "Создать карту")
async def add_card_start(message: types.Message, state: FSMContext):
    ensure_user(message.from_user)
    if not card_admin_guard(message):
        await message.answer("⛔ У тебя нет доступа к добавлению карт.")
        return
    await state.clear()
    await state.set_state(AddCard.waiting_universe)
    await message.answer("➕ <b>Добавление карты</b>\n\nВыбери вселенную, куда добавить карту.", parse_mode="HTML", reply_markup=universe_keyboard("addcard:universe", include_back=True, only_visible=False))


@dp.callback_query(AddCard.waiting_universe, F.data.startswith("addcard:universe:"))
async def add_card_universe(query: types.CallbackQuery, state: FSMContext):
    uid = int(query.data.rsplit(":", 1)[1])
    with closing(db()) as conn:
        universe = conn.execute("SELECT * FROM universes WHERE id=?", (uid,)).fetchone()
    if not universe:
        await query.answer("Вселенная не найдена", show_alert=True)
        return
    await state.update_data(universe_id=uid, universe_name=f"{universe['emoji'] or '🌌'} {universe['name']}")
    await state.set_state(AddCard.waiting_media)
    await edit_or_answer(query, "Отправь изображение карты: фото или GIF-анимацию.", cancel_fsm_keyboard())
    await query.answer()


@dp.message(AddCard.waiting_media, F.photo | F.animation)
async def add_card_media(message: types.Message, state: FSMContext):
    if not card_admin_guard(message):
        return
    if message.animation:
        media_type = "animation"
        file_id = message.animation.file_id
    else:
        media_type = "photo"
        file_id = message.photo[-1].file_id
    file = await bot.get_file(file_id)
    ext = MEDIA_EXT[media_type]
    temp_path = CARD_IMAGE_DIR / f"tmp_{message.from_user.id}_{int(datetime.utcnow().timestamp())}.{ext}"
    await bot.download_file(file.file_path, destination=temp_path)
    await state.update_data(image_path=str(temp_path), media_type=media_type)
    await state.set_state(AddCard.waiting_name)
    await message.answer("✅ Медиа получил. Теперь введи <b>имя персонажа</b>.", parse_mode="HTML", reply_markup=cancel_fsm_keyboard())


@dp.message(AddCard.waiting_media)
async def add_card_media_wrong(message: types.Message):
    await message.answer("Пришли фото или GIF-анимацию карты.", reply_markup=cancel_fsm_keyboard())


@dp.message(AddCard.waiting_name)
async def add_card_name(message: types.Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 1:
        await message.answer("Имя не должно быть пустым.")
        return
    data = await state.get_data()
    await state.update_data(name=name, anime=data.get("universe_name") or "Без категории")
    await state.set_state(AddCard.waiting_rarity)
    kb = rarity_keyboard()
    kb.inline_keyboard.append([InlineKeyboardButton(text="❌ Отмена", callback_data="fsm:cancel")])
    await message.answer("Выбери <b>редкость</b> карты:", parse_mode="HTML", reply_markup=kb)


@dp.callback_query(AddCard.waiting_rarity, F.data.startswith("rarity:"))
async def add_card_rarity(query: types.CallbackQuery, state: FSMContext):
    rarity = query.data.split(":", 1)[1]
    if rarity not in RARITY_CHANCES:
        await query.answer("Неверная редкость", show_alert=True)
        return
    data = await state.get_data()
    universe_emoji = (data.get("universe_name") or "🌌").split(" ", 1)[0]
    await state.update_data(rarity=rarity, emoji=universe_emoji)
    await state.set_state(AddCard.waiting_price)
    await edit_or_answer(query, "Введи <b>цену раскола карты</b> в осколках. Например: 150", cancel_fsm_keyboard())
    await query.answer()


@dp.message(AddCard.waiting_price)
async def add_card_price(message: types.Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Цена должна быть числом. Например: 150")
        return
    price = int(message.text.strip())
    if price <= 0:
        await message.answer("Цена должна быть больше нуля.")
        return
    data = await state.get_data()
    await state.update_data(price=price)
    default_points = DEFAULT_POINTS.get(data.get("rarity"), 10)
    await state.set_state(AddCard.waiting_points)
    await message.answer(f"Введи <b>сколько очков опыта даёт карта</b>.\nРекомендация для этой редкости: <b>{default_points}</b>", parse_mode="HTML", reply_markup=cancel_fsm_keyboard())


@dp.message(AddCard.waiting_points)
async def add_card_points(message: types.Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Очки должны быть числом. Например: 60")
        return
    points = int(message.text.strip())
    if points <= 0:
        await message.answer("Очки должны быть больше нуля.")
        return
    data = await state.get_data()
    old_path = Path(data["image_path"])
    ext = MEDIA_EXT.get(data.get("media_type"), "jpg")
    final_path = CARD_IMAGE_DIR / f"{safe_filename(data['name'])}_{int(datetime.utcnow().timestamp())}.{ext}"
    old_path.rename(final_path)
    await state.update_data(points=points, image_path=str(final_path))
    if not is_admin(message.from_user.id):
        await state.update_data(is_limited=0)
        await state.set_state(AddCard.waiting_confirm)
        await send_card_preview(message.chat.id, await state.get_data())
        return
    await state.set_state(AddCard.waiting_limited)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, лимитка", callback_data="cardlimited:1")],
        [InlineKeyboardButton(text="Нет", callback_data="cardlimited:0")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="fsm:cancel")],
    ])
    await message.answer("Сделать карту лимиткой? Только создатель может создавать лимитки.", reply_markup=kb)


@dp.callback_query(AddCard.waiting_limited, F.data.startswith("cardlimited:"))
async def add_card_limited(query: types.CallbackQuery, state: FSMContext):
    is_limited = 1 if query.data.endswith(":1") else 0
    await state.update_data(is_limited=is_limited)
    await state.set_state(AddCard.waiting_confirm)
    await send_card_preview(query.message.chat.id, await state.get_data())
    await query.answer()


@dp.callback_query(AddCard.waiting_confirm, F.data == "card:publish")
async def publish_card(query: types.CallbackQuery, state: FSMContext):
    if not card_admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    with closing(db()) as conn:
        conn.execute("""
            INSERT INTO card_templates (universe_id, name, anime, rarity, emoji, price, points, image_path, media_type, is_limited, is_active, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (int(data["universe_id"]), data["name"], data["anime"], data["rarity"], data["emoji"], int(data["price"]), int(data["points"]), data.get("image_path"), data.get("media_type", "photo"), int(data.get("is_limited", 0)), query.from_user.id, datetime.utcnow().isoformat()))
    await state.clear()
    await edit_or_answer(query, "✅ Карта опубликована и теперь может выпадать игрокам.", main_menu_keyboard())
    await query.answer()


async def cleanup_card_draft(state: FSMContext) -> None:
    data = await state.get_data()
    path = data.get("image_path")
    if path and Path(path).exists():
        Path(path).unlink(missing_ok=True)


@dp.callback_query(AddCard.waiting_confirm, F.data.in_({"card:restart", "card:cancel"}))
async def cancel_or_restart_card(query: types.CallbackQuery, state: FSMContext):
    await cleanup_card_draft(state)
    restart = query.data == "card:restart"
    await state.clear()
    if restart:
        await state.set_state(AddCard.waiting_universe)
        await edit_or_answer(query, "Ок, начнём заново. Выбери вселенную.", universe_keyboard("addcard:universe", include_back=True, only_visible=False))
    else:
        await edit_or_answer(query, "❌ Добавление карты отменено.", main_menu_keyboard())
    await query.answer()


@dp.callback_query(F.data.in_({"admin:cancel", "fsm:cancel"}))
async def cancel_button(query: types.CallbackQuery, state: FSMContext):
    await cleanup_card_draft(state)
    await state.clear()
    await edit_or_answer(query, "❌ Действие отменено.", main_menu_keyboard())
    await query.answer()


@dp.message(Command("cancel"))
async def cancel(message: types.Message, state: FSMContext):
    await cleanup_card_draft(state)
    await state.clear()
    await message.answer("❌ Действие отменено.", reply_markup=main_menu_keyboard())


@dp.callback_query(F.data == "admin:list_cards")
async def admin_list_cards(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    with closing(db()) as conn:
        rows = conn.execute("SELECT ct.*, u.name AS universe_name, u.emoji AS universe_emoji FROM card_templates ct JOIN universes u ON u.id=ct.universe_id WHERE ct.is_active=1 ORDER BY u.name, ct.rarity, ct.name LIMIT 50").fetchall()
    if not rows:
        await edit_or_answer(query, "Активных карт пока нет.", admin_menu_keyboard())
    else:
        lines = ["📋 <b>Активные карты</b>\nНажми на карту, чтобы удалить её из пула и коллекций игроков.\n"]
        buttons = []
        for i, row in enumerate(rows, 1):
            lines.append(format_card(row, i) + f"\n   🌌 {e(row['universe_emoji'])} {e(row['universe_name'])}")
            buttons.append([InlineKeyboardButton(text=f"🗑 {i}. {row['name']}", callback_data=f"admin:card_delete_confirm:{row['id']}")])
        buttons.append([InlineKeyboardButton(text="⬅️ В админку", callback_data="admin:back")])
        await edit_or_answer(query, "\n\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons))
    await query.answer()


@dp.callback_query(F.data == "admin:shop")
async def admin_shop(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    await edit_or_answer(query, "🛒 <b>Товары магазина</b>\n\nНажми на товар, чтобы скрыть/открыть его.", admin_shop_keyboard())
    await query.answer()


@dp.callback_query(F.data.startswith("admin:shop_toggle:"))
async def admin_shop_toggle(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    item_id = int(query.data.rsplit(":", 1)[1])
    with closing(db()) as conn:
        row = conn.execute("SELECT is_active FROM shop_items WHERE id=?", (item_id,)).fetchone()
        if row:
            conn.execute("UPDATE shop_items SET is_active=? WHERE id=?", (0 if row["is_active"] else 1, item_id))
    await edit_or_answer(query, "✅ Статус товара изменён.", admin_shop_keyboard())
    await query.answer()


@dp.callback_query(F.data == "admin:shop_add")
async def admin_shop_add(query: types.CallbackQuery, state: FSMContext):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddShopItem.waiting_universe)
    kb = universe_keyboard("shopadd:universe", include_back=False, only_visible=False)
    kb.inline_keyboard.insert(0, [InlineKeyboardButton(text="🌐 Для всех вселенных", callback_data="shopadd:universe:0")])
    await edit_or_answer(query, "Выбери, где будет виден товар магазина.", kb)
    await query.answer()


@dp.callback_query(AddShopItem.waiting_universe, F.data.startswith("shopadd:universe:"))
async def shop_add_universe(query: types.CallbackQuery, state: FSMContext):
    uid = int(query.data.rsplit(":", 1)[1])
    await state.update_data(universe_id=(None if uid == 0 else uid))
    await state.set_state(AddShopItem.waiting_type)
    await edit_or_answer(query, "Выбери тип товара.", shop_type_keyboard())
    await query.answer()


@dp.callback_query(AddShopItem.waiting_type, F.data.startswith("shoptype:"))
async def shop_add_type(query: types.CallbackQuery, state: FSMContext):
    item_type = query.data.split(":", 1)[1]
    await state.update_data(item_type=item_type)
    await state.set_state(AddShopItem.waiting_title)
    await edit_or_answer(query, "Введи название товара. Например: 🎟 10 круток или 🔵 Редкая+ карта")
    await query.answer()


@dp.message(AddShopItem.waiting_title)
async def shop_add_title(message: types.Message, state: FSMContext):
    title = (message.text or "").strip()
    if len(title) < 2:
        await message.answer("Название слишком короткое.")
        return
    await state.update_data(title=title)
    await state.set_state(AddShopItem.waiting_cost)
    await message.answer("Введи стоимость товара в осколках.")


@dp.message(AddShopItem.waiting_cost)
async def shop_add_cost(message: types.Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Стоимость должна быть числом.")
        return
    await state.update_data(cost=int(message.text.strip()))
    data = await state.get_data()
    if data["item_type"] == "spins":
        await state.set_state(AddShopItem.waiting_amount)
        await message.answer("Сколько круток выдавать за покупку?")
    else:
        await state.set_state(AddShopItem.waiting_rarity)
        await message.answer("Выбери минимальную редкость карты для товара.", reply_markup=rarity_keyboard("shoprarity"), parse_mode="HTML")


@dp.message(AddShopItem.waiting_amount)
async def shop_add_amount(message: types.Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Количество должно быть числом.")
        return
    data = await state.get_data()
    with closing(db()) as conn:
        conn.execute("INSERT INTO shop_items (universe_id, title, cost, item_type, amount, rarities, is_active, created_by, created_at) VALUES (?, ?, ?, 'spins', ?, NULL, 1, ?, ?)", (data.get("universe_id"), data["title"], int(data["cost"]), int(message.text.strip()), message.from_user.id, datetime.utcnow().isoformat()))
    await state.clear()
    await message.answer("✅ Товар добавлен в магазин.", reply_markup=admin_shop_keyboard())


@dp.callback_query(AddShopItem.waiting_rarity, F.data.startswith("shoprarity:"))
async def shop_add_rarity(query: types.CallbackQuery, state: FSMContext):
    rarity = query.data.split(":", 1)[1]
    order = list(RARITY_CHANCES.keys())
    rarities = order[order.index(rarity):]
    data = await state.get_data()
    with closing(db()) as conn:
        conn.execute("INSERT INTO shop_items (universe_id, title, cost, item_type, amount, rarities, is_active, created_by, created_at) VALUES (?, ?, ?, 'card', 1, ?, 1, ?, ?)", (data.get("universe_id"), data["title"], int(data["cost"]), ",".join(rarities), query.from_user.id, datetime.utcnow().isoformat()))
    await state.clear()
    await edit_or_answer(query, "✅ Товар добавлен в магазин.", admin_shop_keyboard())
    await query.answer()


@dp.callback_query(F.data == "admin:gift_spins")
async def gift_spins_start(query: types.CallbackQuery, state: FSMContext):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await state.set_state(GiftSpins.waiting_user)
    await edit_or_answer(query, "🎁 <b>Подарить крутки</b>\n\nОтправь Telegram ID пользователя. Пользователь должен хотя бы раз открыть бота.")
    await query.answer()


@dp.message(GiftSpins.waiting_user)
async def gift_spins_user(message: types.Message, state: FSMContext):
    row = resolve_user_identifier(message.text or "")
    if not row:
        await message.answer("Не нашёл пользователя. Можно указать ID, @тег или игровой ник. Пользователь должен хотя бы раз открыть бота.")
        return
    await state.update_data(target_user_id=row["id"], target_username=display_name(row))
    await state.set_state(GiftSpins.waiting_amount)
    await message.answer(f"Пользователь: <b>{e(display_name(row))}</b>\nСколько круток подарить?", parse_mode="HTML")


@dp.message(GiftSpins.waiting_amount)
async def gift_spins_amount(message: types.Message, state: FSMContext):
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Количество должно быть числом.")
        return
    amount = int(message.text.strip())
    if amount <= 0:
        await message.answer("Количество должно быть больше нуля.")
        return
    data = await state.get_data()
    target_id = int(data["target_user_id"])
    with closing(db()) as conn:
        conn.execute("UPDATE users SET spins=spins+? WHERE id=?", (amount, target_id))
    delivered = True
    try:
        await bot.send_message(target_id, f"🎁 Тебе подарили крутки!\n\n🎟 Получено: <b>{amount}</b>", parse_mode="HTML")
    except (TelegramForbiddenError, TelegramBadRequest):
        delivered = False
    await state.clear()
    note = "Уведомление отправлено пользователю." if delivered else "Крутки начислены, но уведомление отправить не удалось: пользователь не писал боту или заблокировал его."
    await message.answer(f"✅ Крутки начислены: <b>{amount}</b>\n{note}", parse_mode="HTML", reply_markup=admin_menu_keyboard())


async def do_drop(target, user: types.User):
    universe_id = await require_universe(target, user)
    if universe_id is None:
        return
    now = datetime.utcnow()
    used_spin = False
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        user_row = conn.execute("SELECT * FROM users WHERE id=?", (user.id,)).fetchone()
        if user_row["spins"] and user_row["spins"] > 0:
            res = conn.execute("UPDATE users SET spins=spins-1 WHERE id=? AND spins>0", (user.id,))
            if res.rowcount != 1:
                conn.rollback(); await edit_or_answer(target, "Крутка уже была использована другим запросом.", main_menu_keyboard()); return
            used_spin = True
        elif user_row["last_drop_at"]:
            next_drop = datetime.fromisoformat(user_row["last_drop_at"]) + timedelta(minutes=drop_cooldown_minutes(conn, user.id))
            if now < next_drop:
                conn.rollback()
                await edit_or_answer(target, f"⏳ Следующая бесплатная карта через <b>{seconds_to_text(int((next_drop-now).total_seconds()))}</b>.", main_menu_keyboard())
                return
        try:
            card = pick_card_template(conn, universe_id, user_id=user.id)
        except RuntimeError as exc:
            conn.rollback(); await edit_or_answer(target, f"⚠️ {e(exc)}", main_menu_keyboard()); return
        points, shards = grant_card(conn, user.id, card, now)
        referral_completed = complete_referral_if_ready(conn, user.id)
        if used_spin:
            conn.execute("UPDATE users SET drops=drops+1 WHERE id=?", (user.id,))
        else:
            conn.execute("UPDATE users SET drops=drops+1, last_drop_at=? WHERE id=?", (now.isoformat(), user.id))
        new_user = conn.execute("SELECT spins, coins, shards FROM users WHERE id=?", (user.id,)).fetchone()
        conn.commit()
    caption = ("🎟 <b>Крутка использована!</b>\n\n" if used_spin else "🎉 <b>Новая карта!</b>\n\n") + format_card(card)
    caption += "\n\n" + format_card_reward(points, new_user["coins"], shards, new_user["shards"], new_user["spins"])
    if referral_completed:
        caption += f"\n\n🔗 Реферальное задание выполнено! Начислено <b>{REFERRAL_REWARD_SPINS}</b> круток тебе и пригласившему игроку."
    await send_card_result(target, caption, card)


@dp.message(Command("drop"))
@dp.message(Command("card"))
@dp.message(F.text.in_({"Получить карту", "🎴 Получить карту", "🎴 Карта"}))
async def drop(message: types.Message):
    await do_drop(message, message.from_user)


@dp.callback_query(F.data == "menu:drop")
async def drop_button(query: types.CallbackQuery):
    await do_drop(query, query.from_user)
    await query.answer()


async def do_daily(target, user: types.User):
    ensure_user(user)
    today = date.today()
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM users WHERE id=?", (user.id,)).fetchone()
        if row["last_daily_at"] == today.isoformat():
            conn.rollback(); await edit_or_answer(target, "🎁 Сегодня бонус уже забран.", main_menu_keyboard()); return
        yesterday = today - timedelta(days=1)
        streak = min(7, int(row["daily_streak"] or 0) + 1) if row["last_daily_at"] == yesterday.isoformat() else 1
        reward = streak
        res = conn.execute("UPDATE users SET spins=spins+?, daily_streak=?, last_daily_at=? WHERE id=? AND (last_daily_at IS NULL OR last_daily_at<>?)", (reward, streak, today.isoformat(), user.id, today.isoformat()))
        if res.rowcount != 1:
            conn.rollback(); await edit_or_answer(target, "🎁 Сегодня бонус уже забран.", main_menu_keyboard()); return
        spins = conn.execute("SELECT spins FROM users WHERE id=?", (user.id,)).fetchone()["spins"]
        conn.commit()
    await edit_or_answer(target, f"🎁 <b>Ежедневный бонус получен!</b>\n\n🔥 День серии: <b>{streak}/7</b>\n🎟 Начислено круток: <b>{reward}</b>\n🎟 Всего круток: <b>{spins}</b>", main_menu_keyboard())


@dp.message(Command("daily"))
@dp.message(F.text == "🎁 Бонус")
async def daily(message: types.Message):
    await do_daily(message, message.from_user)


@dp.callback_query(F.data == "menu:daily")
async def daily_button(query: types.CallbackQuery):
    await do_daily(query, query.from_user)
    await query.answer()


async def profile_text(user_id: int, full_name: str) -> str:
    with closing(db()) as conn:
        user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        universe = get_active_universe(conn, user_id)
        uid = universe["id"] if universe else None
        stats = conn.execute("SELECT COUNT(*) AS total, COUNT(DISTINCT template_id) AS unique_cards, COALESCE(SUM(price),0) AS value FROM cards WHERE user_id=? AND universe_id=?", (user_id, uid)).fetchone() if uid else {"total": 0, "unique_cards": 0, "value": 0}
        referrals = conn.execute("SELECT COUNT(*) AS total FROM users WHERE referrer_id=?", (user_id,)).fetchone()["total"]
    premium = "активен" if is_premium_row(user) else "нет"
    cooldown = PREMIUM_DROP_COOLDOWN_MINUTES if is_premium_row(user) else DROP_COOLDOWN_MINUTES
    return f"👤 <b>{e(display_name(user))}</b>\n\n🌌 Вселенная: <b>{e((universe['emoji'] + ' ' + universe['name']) if universe else 'не выбрана')}</b>\n⭐ Очки: <b>{user['coins']}</b>\n💎 Осколки: <b>{user['shards']}</b>\n🎟 Крутки: <b>{user['spins']}</b>\n💫 Премиум: <b>{premium}</b>\n⏳ Ожидание карты: <b>{cooldown} мин.</b>\n🔗 Приглашено друзей: <b>{referrals}</b>\n🎴 Карт в этой вселенной: <b>{stats['total']}</b>\n🌟 Уникальных: <b>{stats['unique_cards']}</b>\n💠 Ценность раскола: <b>{stats['value']}</b>\n📦 Получено карт: <b>{user['drops']}</b>"


@dp.message(Command("nick"))
async def nick_command(message: types.Message, state: FSMContext):
    ensure_user(message.from_user)
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) == 2 and parts[1].strip():
        nick = parts[1].strip()[:32]
        with closing(db()) as conn:
            conn.execute("UPDATE users SET nickname=? WHERE id=?", (nick, message.from_user.id))
        await message.answer(f"✅ Игровой ник изменён: <b>{e(nick)}</b>", parse_mode="HTML", reply_markup=main_menu_keyboard())
    else:
        await state.set_state(NicknameChange.waiting_nick)
        await message.answer("✏️ Введи новый игровой ник. До 32 символов. Отмена: /cancel")


@dp.message(NicknameChange.waiting_nick)
async def nick_waiting(message: types.Message, state: FSMContext):
    nick = (message.text or "").strip()[:32]
    if len(nick) < 2:
        await message.answer("Ник слишком короткий.")
        return
    with closing(db()) as conn:
        conn.execute("UPDATE users SET nickname=? WHERE id=?", (nick, message.from_user.id))
    await state.clear()
    await message.answer(f"✅ Игровой ник изменён: <b>{e(nick)}</b>", parse_mode="HTML", reply_markup=main_menu_keyboard())

@dp.message(Command("profile"))
@dp.message(F.text.in_({"Профиль", "👤 Профиль"}))
async def profile(message: types.Message):
    ensure_user(message.from_user)
    target_id = message.from_user.id
    if message.reply_to_message and message.reply_to_message.from_user:
        ensure_user(message.reply_to_message.from_user)
        target_id = message.reply_to_message.from_user.id
    else:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            row = resolve_user_identifier(parts[1])
            if not row:
                await message.answer("Пользователь не найден. Он должен хотя бы раз открыть бота.")
                return
            target_id = row["id"]
    await message.answer(
        await profile_text(target_id, message.from_user.full_name),
        parse_mode="HTML",
        reply_markup=profile_keyboard(target_id == message.from_user.id),
    )


@dp.callback_query(F.data == "menu:profile")
async def profile_button(query: types.CallbackQuery):
    ensure_user(query.from_user)
    await edit_or_answer(query, await profile_text(query.from_user.id, query.from_user.full_name), profile_keyboard(True))
    await query.answer()


def collection_universes(user_id: int) -> list[sqlite3.Row]:
    with closing(db()) as conn:
        rows = conn.execute("""
            SELECT DISTINCT u.*
            FROM universes u
            WHERE u.id IN (
                SELECT universe_id FROM user_universes WHERE user_id=?
                UNION
                SELECT universe_id FROM cards WHERE user_id=?
                UNION
                SELECT active_universe_id FROM users WHERE id=? AND active_universe_id IS NOT NULL
            )
            ORDER BY u.name
        """, (user_id, user_id, user_id)).fetchall()
    return rows


def collection_universe_picker(user_id: int) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{row['emoji'] or '🌌'} {row['name']}", callback_data=f"collection:universe:{row['id']}")]
        for row in collection_universes(user_id)
    ]
    rows.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def collection_text(user_id: int, universe_id: int | None = None) -> str:
    with closing(db()) as conn:
        universe = conn.execute("SELECT * FROM universes WHERE id=?", (universe_id,)).fetchone() if universe_id else get_active_universe(conn, user_id)
        if not universe:
            return "🌌 Сначала выбери вселенную."
        uid = universe["id"]
        totals = {r["rarity"]: r["total"] for r in conn.execute("SELECT rarity, COUNT(*) AS total FROM card_templates WHERE is_active=1 AND universe_id=? GROUP BY rarity", (uid,)).fetchall()}
        owned = {r["rarity"]: r["owned"] for r in conn.execute("SELECT rarity, COUNT(DISTINCT template_id) AS owned FROM cards WHERE user_id=? AND universe_id=? GROUP BY rarity", (user_id, uid)).fetchall()}
    progress = [f"{RARITY_STYLE[r]} — {owned.get(r,0)}/{totals.get(r,0)}" for r in RARITY_STYLE]
    return f"🎒 <b>Твоя коллекция</b>\n🌌 Вселенная: <b>{e(universe['emoji'])} {e(universe['name'])}</b>\n\n" + "\n".join(progress)


def collection_cards(user_id: int, universe_id: int, rarity: str) -> list[sqlite3.Row]:
    with closing(db()) as conn:
        universe = conn.execute("SELECT * FROM universes WHERE id=?", (universe_id,)).fetchone()
        if not universe:
            return []
        return conn.execute("""
            SELECT template_id, MIN(name) AS name, MIN(anime) AS anime, MIN(rarity) AS rarity, MIN(emoji) AS emoji,
                   MIN(price) AS price, MIN(points) AS points, MIN(image_path) AS image_path, MIN(media_type) AS media_type, MAX(is_limited) AS is_limited, COUNT(*) AS copies
            FROM cards
            WHERE user_id=? AND universe_id=? AND rarity=?
            GROUP BY template_id
            ORDER BY price DESC, name
        """, (user_id, universe_id, rarity)).fetchall()


def collection_card_keyboard(universe_id: int, rarity: str, index: int, total: int) -> InlineKeyboardMarkup:
    nav = []
    if total > 1:
        nav = [
            InlineKeyboardButton(text="◀️", callback_data=f"collection:view:{universe_id}:{rarity}:{max(0, index-1)}"),
            InlineKeyboardButton(text=f"{index+1}/{total}", callback_data="noop"),
            InlineKeyboardButton(text="▶️", callback_data=f"collection:view:{universe_id}:{rarity}:{min(total-1, index+1)}"),
        ]
    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К редкостям", callback_data=f"collection:universe:{universe_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_collection_card(query: types.CallbackQuery, universe_id: int, rarity: str, index: int = 0) -> None:
    rows = collection_cards(query.from_user.id, universe_id, rarity)
    if not rows:
        await edit_or_answer(query, f"{RARITY_STYLE.get(rarity, rarity)}\n\nПока нет карт этой редкости.", collection_filter_keyboard(query.from_user.id, universe_id))
        await query.answer()
        return
    index = max(0, min(index, len(rows) - 1))
    card = rows[index]
    caption = format_card(card, index + 1, card["copies"])
    markup = collection_card_keyboard(universe_id, rarity, index, len(rows))
    path = card["image_path"] if "image_path" in card.keys() else None
    media_type = card["media_type"] if "media_type" in card.keys() else "photo"
    try:
        await query.message.delete()
    except TelegramBadRequest:
        pass
    if path and Path(path).exists() and media_type == "animation":
        await bot.send_animation(query.message.chat.id, types.FSInputFile(path), caption=caption, parse_mode="HTML", reply_markup=markup)
    elif path and Path(path).exists():
        await bot.send_photo(query.message.chat.id, types.FSInputFile(path), caption=caption, parse_mode="HTML", reply_markup=markup)
    else:
        await bot.send_message(query.message.chat.id, caption, parse_mode="HTML", reply_markup=markup)
    await query.answer()


@dp.message(Command("collection"))
@dp.message(F.text.in_({"Мои карты", "🎒 Коллекция", "🎒 Карты"}))
async def collection(message: types.Message):
    ensure_user(message.from_user)
    universes = collection_universes(message.from_user.id)
    if len(universes) > 1:
        await message.answer("🎒 <b>Выбери вселенную коллекции</b>", reply_markup=collection_universe_picker(message.from_user.id), parse_mode="HTML")
        return
    uid = universes[0]["id"] if universes else await require_universe(message, message.from_user)
    if uid is None:
        return
    await message.answer(await collection_text(message.from_user.id, uid), reply_markup=collection_filter_keyboard(message.from_user.id, uid), parse_mode="HTML")


@dp.callback_query(F.data == "menu:collection")
async def collection_button(query: types.CallbackQuery):
    universes = collection_universes(query.from_user.id)
    if len(universes) > 1:
        await edit_or_answer(query, "🎒 <b>Выбери вселенную коллекции</b>", collection_universe_picker(query.from_user.id))
        await query.answer()
        return
    uid = universes[0]["id"] if universes else await require_universe(query, query.from_user)
    if uid is None: await query.answer(); return
    await edit_or_answer(query, await collection_text(query.from_user.id, uid), collection_filter_keyboard(query.from_user.id, uid))
    await query.answer()


@dp.callback_query(F.data.startswith("collection:view:"))
async def collection_view_page(query: types.CallbackQuery):
    _, _, uid, rarity, index = query.data.split(":", 4)
    if rarity not in RARITY_STYLE:
        await query.answer("Неизвестная редкость", show_alert=True); return
    await show_collection_card(query, int(uid), rarity, int(index))


@dp.callback_query(F.data.startswith("collection:universe:"))
async def collection_universe_page(query: types.CallbackQuery):
    uid = int(query.data.rsplit(":", 1)[1])
    await edit_or_answer(query, await collection_text(query.from_user.id, uid), collection_filter_keyboard(query.from_user.id, uid))
    await query.answer()


@dp.callback_query(F.data.startswith("collection:"))
async def collection_by_rarity(query: types.CallbackQuery):
    parts = query.data.split(":")
    if len(parts) == 3:
        uid = int(parts[1])
        rarity = parts[2]
    else:
        with closing(db()) as conn:
            uid = active_universe_id(conn, query.from_user.id) or 0
        rarity = parts[1]
    if rarity == "All":
        await edit_or_answer(query, await collection_text(query.from_user.id, uid), collection_filter_keyboard(query.from_user.id, uid))
        await query.answer(); return
    if rarity not in RARITY_STYLE:
        await query.answer("Неизвестная редкость", show_alert=True); return
    await show_collection_card(query, uid, rarity, 0)


@dp.callback_query(F.data == "noop")
async def noop_callback(query: types.CallbackQuery):
    await query.answer()


async def sell_duplicates_do(target, user: types.User):
    uid = await require_universe(target, user)
    if uid is None: return
    earned = sold = 0
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        groups = conn.execute("SELECT template_id, MIN(id) AS keep_id, COUNT(*) AS copies FROM cards WHERE user_id=? AND universe_id=? GROUP BY template_id HAVING COUNT(*)>1", (user.id, uid)).fetchall()
        for g in groups:
            dups = conn.execute("SELECT id, price FROM cards WHERE user_id=? AND universe_id=? AND template_id=? AND id<>?", (user.id, uid, g["template_id"], g["keep_id"])).fetchall()
            for d in dups:
                earned += max(1, int(d["price"] * 0.4)); sold += 1
                conn.execute("DELETE FROM cards WHERE id=?", (d["id"],))
        if sold:
            conn.execute("UPDATE users SET shards=shards+? WHERE id=?", (earned, user.id))
        conn.commit()
    await edit_or_answer(target, "✅ Дублей нет." if not sold else f"💎 Расколото дублей: <b>{sold}</b>\nПолучено осколков: <b>{earned}</b>", main_menu_keyboard())


@dp.message(Command("dupes"))
@dp.message(F.text == "💎 Раскол дублей")
async def dupes(message: types.Message):
    await sell_duplicates_do(message, message.from_user)


@dp.callback_query(F.data == "menu:dupes")
async def dupes_button(query: types.CallbackQuery):
    await sell_duplicates_do(query, query.from_user)
    await query.answer()


async def shop_text(user_id: int) -> str:
    with closing(db()) as conn:
        user = conn.execute("SELECT shards, spins, premium_until FROM users WHERE id=?", (user_id,)).fetchone()
        universe = get_active_universe(conn, user_id)
        count = conn.execute("SELECT COUNT(*) AS total FROM shop_items WHERE is_active=1 AND (universe_id IS NULL OR universe_id=?)", (universe["id"] if universe else None,)).fetchone()["total"]
    premium = "активен" if is_premium_row(user) else "нет"
    return f"🛒 <b>Магазин</b>\n\n🌌 Вселенная: <b>{e((universe['emoji'] + ' ' + universe['name']) if universe else 'не выбрана')}</b>\n💎 Твои осколки: <b>{user['shards']}</b>\n🎟 Крутки: <b>{user['spins']}</b>\n💫 Премиум: <b>{premium}</b>\n🛍 Доступно товаров: <b>{count}</b>"


def shop_has_items(user_id: int) -> bool:
    with closing(db()) as conn:
        universe_id = active_universe_id(conn, user_id)
        count = conn.execute("SELECT COUNT(*) AS total FROM shop_items WHERE is_active=1 AND (universe_id IS NULL OR universe_id=?)", (universe_id,)).fetchone()["total"]
    return count > 0


@dp.message(Command("shop"))
@dp.message(F.text == "🛒 Магазин")
async def shop(message: types.Message):
    ensure_user(message.from_user)
    if not shop_has_items(message.from_user.id):
        await message.answer("Извините, в данный момент в Магазине отсутствуют товары.", reply_markup=back_to_menu_keyboard())
        return
    await message.answer(await shop_text(message.from_user.id), reply_markup=shop_keyboard(message.from_user.id), parse_mode="HTML")


@dp.callback_query(F.data == "menu:shop")
async def shop_button(query: types.CallbackQuery):
    ensure_user(query.from_user)
    if not shop_has_items(query.from_user.id):
        await edit_or_answer(query, "Извините, в данный момент в Магазине отсутствуют товары.", back_to_menu_keyboard())
        await query.answer()
        return
    await edit_or_answer(query, await shop_text(query.from_user.id), shop_keyboard(query.from_user.id))
    await query.answer()


@dp.callback_query(F.data == "shop:donate")
async def donate_tab(query: types.CallbackQuery):
    ensure_user(query.from_user)
    text = (
        "⭐ <b>Поддержать проект</b>\n\n"
        "Покупки проходят через Telegram Stars. Крутки начисляются сразу после успешной оплаты.\n"
        f"Премиум действует <b>{PREMIUM_DAYS}</b> дней: ожидание карты {PREMIUM_DROP_COOLDOWN_MINUTES} минут и улучшенные шансы редких карт."
    )
    await edit_or_answer(query, text, donate_keyboard())
    await query.answer()


@dp.message(Command("donate"))
@dp.message(F.text.in_({"⭐ Донат", "⭐ Поддержать проект"}))
async def donate_message(message: types.Message):
    ensure_user(message.from_user)
    text = (
        "⭐ <b>Поддержать проект</b>\n\n"
        "Покупки проходят через Telegram Stars. Крутки начисляются сразу после успешной оплаты.\n"
        f"Премиум действует <b>{PREMIUM_DAYS}</b> дней: ожидание карты {PREMIUM_DROP_COOLDOWN_MINUTES} минут и улучшенные шансы редких карт."
    )
    await message.answer(text, reply_markup=donate_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("donate:spins:"))
async def donate_spins_invoice(query: types.CallbackQuery):
    ensure_user(query.from_user)
    package_id = query.data.rsplit(":", 1)[1]
    package = DONATE_SPIN_PACKAGES.get(package_id)
    if not package:
        await query.answer("Пакет не найден", show_alert=True); return
    await bot.send_invoice(
        chat_id=query.message.chat.id,
        title=f"{package['spins']} круток",
        description="Крутки для получения карт в HenCard",
        payload=f"donate:spins:{package_id}:{query.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[types.LabeledPrice(label=f"{package['spins']} круток", amount=package["stars"])],
    )
    await query.answer()


@dp.callback_query(F.data == "donate:premium")
async def donate_premium_invoice(query: types.CallbackQuery):
    ensure_user(query.from_user)
    await bot.send_invoice(
        chat_id=query.message.chat.id,
        title=f"Премиум на {PREMIUM_DAYS} дней",
        description=f"Ожидание карты {PREMIUM_DROP_COOLDOWN_MINUTES} минут и улучшенные шансы редких карт",
        payload=f"donate:premium:{query.from_user.id}",
        provider_token="",
        currency="XTR",
        prices=[types.LabeledPrice(label="Премиум", amount=PREMIUM_STARS_COST)],
    )
    await query.answer()


def save_payment(user: types.User, payment: types.SuccessfulPayment, item_type: str, item_title: str, reward_amount: int = 0) -> None:
    with closing(db()) as conn:
        conn.execute(
            """
            INSERT INTO payments (
                user_id, username, item_type, item_title, stars, reward_amount, currency, payload,
                telegram_payment_charge_id, provider_payment_charge_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user.id,
                user.username or user.full_name,
                item_type,
                item_title,
                int(payment.total_amount or 0),
                reward_amount,
                payment.currency,
                payment.invoice_payload,
                payment.telegram_payment_charge_id,
                payment.provider_payment_charge_id,
                datetime.utcnow().isoformat(),
            ),
        )


@dp.pre_checkout_query()
async def process_pre_checkout(query: types.PreCheckoutQuery):
    await query.answer(ok=True)


@dp.message(F.successful_payment)
async def process_successful_payment(message: types.Message):
    ensure_user(message.from_user)
    payment = message.successful_payment
    payload = payment.invoice_payload
    parts = payload.split(":")
    if len(parts) < 3 or parts[0] != "donate":
        save_payment(message.from_user, payment, "unknown", "Неизвестная покупка", 0)
        await message.answer("✅ Оплата получена.", reply_markup=main_menu_keyboard())
        return
    if int(parts[-1]) != message.from_user.id:
        save_payment(message.from_user, payment, "mismatch", "Оплата с неверным пользователем", 0)
        await message.answer("⚠️ Оплата получена, но пользователь не совпал. Напиши администратору.", reply_markup=main_menu_keyboard())
        return
    if parts[1] == "spins" and len(parts) == 4:
        package = DONATE_SPIN_PACKAGES.get(parts[2])
        if not package:
            await message.answer("⚠️ Пакет не найден. Напиши администратору.", reply_markup=main_menu_keyboard())
            return
        with closing(db()) as conn:
            conn.execute("UPDATE users SET spins=spins+? WHERE id=?", (package["spins"], message.from_user.id))
            total = conn.execute("SELECT spins FROM users WHERE id=?", (message.from_user.id,)).fetchone()["spins"]
        save_payment(message.from_user, payment, "spins", f"{package['spins']} круток", package["spins"])
        await message.answer(f"✅ Начислено круток: <b>{package['spins']}</b>\n🎟 Всего круток: <b>{total}</b>", parse_mode="HTML", reply_markup=main_menu_keyboard())
        return
    if parts[1] == "premium":
        with closing(db()) as conn:
            row = conn.execute("SELECT premium_until FROM users WHERE id=?", (message.from_user.id,)).fetchone()
            base = datetime.utcnow()
            if is_premium_row(row):
                base = datetime.fromisoformat(row["premium_until"])
            premium_until = base + timedelta(days=PREMIUM_DAYS)
            conn.execute("UPDATE users SET premium_until=? WHERE id=?", (premium_until.isoformat(), message.from_user.id))
        save_payment(message.from_user, payment, "premium", f"Премиум на {PREMIUM_DAYS} дней", PREMIUM_DAYS)
        await message.answer(f"✅ Премиум активирован до <b>{premium_until.strftime('%d.%m.%Y %H:%M')}</b>", parse_mode="HTML", reply_markup=main_menu_keyboard())
        return
    save_payment(message.from_user, payment, "unknown", "Неизвестная покупка", 0)
    await message.answer("✅ Оплата получена.", reply_markup=main_menu_keyboard())


@dp.callback_query(F.data.startswith("shop:buy:"))
async def buy_shop_item(query: types.CallbackQuery):
    ensure_user(query.from_user)
    item_id = int(query.data.rsplit(":", 1)[1])
    now = datetime.utcnow()
    with closing(db()) as conn:
        uid = active_universe_id(conn, query.from_user.id)
        conn.execute("BEGIN IMMEDIATE")
        item = conn.execute("SELECT * FROM shop_items WHERE id=? AND is_active=1 AND (universe_id IS NULL OR universe_id=?)", (item_id, uid)).fetchone()
        if not item:
            conn.rollback(); await query.answer("Товар не найден", show_alert=True); return
        if item["item_type"] != "spins" and not uid:
            conn.rollback()
            await edit_or_answer(query, "🌌 Для покупки карты сначала выбери вселенную. Донат и товары с крутками доступны без выбора вселенной.", universe_keyboard("universe:select", include_back=True))
            await query.answer()
            return
        res = conn.execute("UPDATE users SET shards=shards-? WHERE id=? AND shards>=?", (item["cost"], query.from_user.id, item["cost"]))
        if res.rowcount != 1:
            conn.rollback(); await query.answer("Не хватает осколков", show_alert=True); return
        if item["item_type"] == "spins":
            conn.execute("UPDATE users SET spins=spins+? WHERE id=?", (item["amount"], query.from_user.id))
            total = conn.execute("SELECT shards, spins FROM users WHERE id=?", (query.from_user.id,)).fetchone()
            conn.commit()
            await edit_or_answer(query, f"✅ Куплено: <b>{e(item['title'])}</b>\n💎 Осталось: <b>{total['shards']}</b>\n🎟 Круток: <b>{total['spins']}</b>", shop_keyboard(query.from_user.id))
        else:
            rarities = [x for x in (item["rarities"] or "").split(",") if x]
            try:
                card = pick_card_template(conn, uid, rarities, user_id=query.from_user.id)
            except RuntimeError as exc:
                conn.rollback(); await edit_or_answer(query, f"⚠️ {e(exc)}", shop_keyboard(query.from_user.id)); return
            points, shards = grant_card(conn, query.from_user.id, card, now)
            referral_completed = complete_referral_if_ready(conn, query.from_user.id)
            total = conn.execute("SELECT shards, coins FROM users WHERE id=?", (query.from_user.id,)).fetchone()
            conn.commit()
            referral_note = f"\n\n🔗 Реферальное задание выполнено! Начислено <b>{REFERRAL_REWARD_SPINS}</b> круток тебе и пригласившему игроку." if referral_completed else ""
            await edit_or_answer(query, f"✅ Куплено: <b>{e(item['title'])}</b>\n\n{format_card(card)}\n\n{format_card_reward(points, total['coins'], shards, total['shards'])}{referral_note}", shop_keyboard(query.from_user.id))
    await query.answer()


async def universe_text(user_id: int) -> str:
    with closing(db()) as conn:
        user = conn.execute("SELECT coins FROM users WHERE id=?", (user_id,)).fetchone()
        universe = get_active_universe(conn, user_id)
        count = conn.execute("SELECT COUNT(*) AS total FROM universes WHERE is_visible=1").fetchone()["total"]
    current = f"{universe['emoji']} {universe['name']}" if universe else "не выбрана"
    return f"🌌 <b>Вселенная</b>\n\nТекущая: <b>{e(current)}</b>\nДоступно для выбора: <b>{count}</b>\nСтоимость смены: <b>{universe_change_cost()}</b> ⭐\n\nПри смене вселенной старые карты сохраняются в коллекции. Новые карты будут выпадать только из выбранной вселенной."


def universe_change_keyboard(user_id: int) -> InlineKeyboardMarkup:
    with closing(db()) as conn:
        current = active_universe_id(conn, user_id)
        rows = conn.execute("SELECT * FROM universes WHERE is_visible=1 ORDER BY name").fetchall()
    kb = []
    for row in rows:
        label = f"✅ {row['emoji']} {row['name']}" if row["id"] == current else f"{row['emoji']} {row['name']}"
        kb.append([InlineKeyboardButton(text=label, callback_data=f"universe:confirm:{row['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


@dp.message(Command("universe"))
@dp.message(F.text == "🌌 Вселенная")
async def universe_menu(message: types.Message):
    ensure_user(message.from_user)
    await message.answer(await universe_text(message.from_user.id), reply_markup=universe_change_keyboard(message.from_user.id), parse_mode="HTML")


@dp.callback_query(F.data == "menu:universe")
async def universe_button(query: types.CallbackQuery):
    ensure_user(query.from_user)
    await edit_or_answer(query, await universe_text(query.from_user.id), universe_change_keyboard(query.from_user.id))
    await query.answer()


@dp.callback_query(F.data.startswith("universe:confirm:"))
async def confirm_change_universe(query: types.CallbackQuery):
    ensure_user(query.from_user)
    new_uid = int(query.data.rsplit(":", 1)[1])
    with closing(db()) as conn:
        universe = conn.execute("SELECT * FROM universes WHERE id=? AND is_visible=1", (new_uid,)).fetchone()
        current = active_universe_id(conn, query.from_user.id)
    if not universe:
        await query.answer("Вселенная закрыта", show_alert=True)
        return
    if current == new_uid:
        await query.answer("Это уже твоя вселенная", show_alert=True)
        return
    if current is None:
        await change_universe(query)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Подтвердить смену", callback_data=f"universe:change:{new_uid}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="menu:universe")],
    ])
    await edit_or_answer(query, f"⚠️ <b>Подтверди смену вселенной</b>\n\nБудет списано: <b>{universe_change_cost()}</b> ⭐\nКарты старой вселенной сохранятся в коллекции.\nНовая вселенная: <b>{e(universe['emoji'])} {e(universe['name'])}</b>", kb)
    await query.answer()


@dp.callback_query(F.data.startswith("universe:change:"))
async def change_universe(query: types.CallbackQuery):
    ensure_user(query.from_user)
    new_uid = int(query.data.rsplit(":", 1)[1])
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        universe = conn.execute("SELECT * FROM universes WHERE id=? AND is_visible=1", (new_uid,)).fetchone()
        if not universe:
            conn.rollback(); await query.answer("Вселенная закрыта", show_alert=True); return
        current = active_universe_id(conn, query.from_user.id)
        if current == new_uid:
            conn.rollback(); await query.answer("Это уже твоя вселенная", show_alert=True); return
        user = conn.execute("SELECT coins FROM users WHERE id=?", (query.from_user.id,)).fetchone()
        if current is not None and user["coins"] < universe_change_cost():
            conn.rollback(); await query.answer(f"Нужно {universe_change_cost()} очков", show_alert=True); return
        if current is not None:
            conn.execute("UPDATE users SET coins=coins-?, active_universe_id=? WHERE id=?", (universe_change_cost(), new_uid, query.from_user.id))
        else:
            conn.execute("UPDATE users SET active_universe_id=? WHERE id=?", (new_uid, query.from_user.id))
        remember_user_universe(conn, query.from_user.id, new_uid)
        conn.commit()
    await edit_or_answer(query, f"✅ Вселенная изменена на <b>{e(universe['emoji'])} {e(universe['name'])}</b>.\n\nСтарые карты сохранены в коллекции.\n⭐ Стоимость смены: <b>{0 if current is None else universe_change_cost()}</b>", main_menu_keyboard())
    await query.answer()



@dp.callback_query(F.data.startswith("admin:shop_item:"))
async def admin_shop_item(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True); return
    item_id = int(query.data.rsplit(":",1)[1])
    with closing(db()) as conn:
        item = conn.execute("SELECT * FROM shop_items WHERE id=?", (item_id,)).fetchone()
    if not item:
        await query.answer("Товар не найден", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅/🔒 Скрыть или открыть", callback_data=f"admin:shop_toggle:{item_id}")],
        [InlineKeyboardButton(text="🗑 Удалить товар", callback_data=f"admin:shop_delete_confirm:{item_id}")],
        [InlineKeyboardButton(text="⬅️ К товарам", callback_data="admin:shop")],
    ])
    await edit_or_answer(query, f"🛒 <b>{e(item['title'])}</b>\nСтоимость: <b>{item['cost']}</b> 💎\nСтатус: <b>{'активен' if item['is_active'] else 'скрыт'}</b>", kb)
    await query.answer()


@dp.callback_query(F.data.startswith("admin:shop_delete_confirm:"))
async def admin_shop_delete_confirm(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True); return
    item_id = int(query.data.rsplit(":",1)[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить товар", callback_data=f"admin:shop_delete:{item_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:shop")],
    ])
    await edit_or_answer(query, "⚠️ Удалить товар из магазина? Это действие нельзя отменить.", kb)
    await query.answer()


@dp.callback_query(F.data.startswith("admin:shop_delete:"))
async def admin_shop_delete(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True); return
    item_id = int(query.data.rsplit(":",1)[1])
    with closing(db()) as conn:
        conn.execute("DELETE FROM shop_items WHERE id=?", (item_id,))
    await edit_or_answer(query, "🗑 Товар удалён.", admin_shop_keyboard())
    await query.answer()


@dp.callback_query(F.data.startswith("admin:card_delete_confirm:"))
async def admin_card_delete_confirm(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True); return
    card_id = int(query.data.rsplit(":",1)[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить карту", callback_data=f"admin:card_delete:{card_id}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:list_cards")],
    ])
    await edit_or_answer(query, "⚠️ Карта будет удалена из пула и коллекций игроков. Подтвердить?", kb)
    await query.answer()


@dp.callback_query(F.data.startswith("admin:card_delete:"))
async def admin_card_delete(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True); return
    card_id = int(query.data.rsplit(":",1)[1])
    with closing(db()) as conn:
        conn.execute("UPDATE card_templates SET is_active=0 WHERE id=?", (card_id,))
        conn.execute("DELETE FROM cards WHERE template_id=? AND is_limited=0", (card_id,))
    await edit_or_answer(query, "🗑 Карта удалена из пула и коллекций игроков.", admin_menu_keyboard())
    await query.answer()


@dp.callback_query(F.data.startswith("admin:universe_delete_confirm:"))
async def admin_universe_delete_confirm(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True); return
    uid = int(query.data.rsplit(":",1)[1])
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, удалить вселенную", callback_data=f"admin:universe_delete:{uid}")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:universes")],
    ])
    await edit_or_answer(query, "⚠️ Вселенная будет удалена вместе с картами, товарами и выбором игроков. Подтвердить?", kb)
    await query.answer()


@dp.callback_query(F.data.startswith("admin:universe_delete:"))
async def admin_universe_delete(query: types.CallbackQuery):
    if not admin_guard(query):
        await query.answer("Нет доступа", show_alert=True); return
    uid = int(query.data.rsplit(":",1)[1])
    with closing(db()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM cards WHERE universe_id=?", (uid,))
        conn.execute("DELETE FROM card_templates WHERE universe_id=?", (uid,))
        conn.execute("DELETE FROM shop_items WHERE universe_id=?", (uid,))
        conn.execute("UPDATE users SET active_universe_id=NULL WHERE active_universe_id=?", (uid,))
        conn.execute("DELETE FROM universes WHERE id=?", (uid,))
        conn.commit()
    await edit_or_answer(query, "🗑 Вселенная удалена вместе с её картами и товарами.", admin_universes_keyboard())
    await query.answer()


@dp.callback_query(F.data == "admin:settings")
async def admin_settings(query: types.CallbackQuery):
    if not admin_guard(query): await query.answer("Нет доступа", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"🌌 Цена смены вселенной: {universe_change_cost()} ⭐", callback_data="settings:universe_change_cost")],
        [InlineKeyboardButton(text="⬅️ В админку", callback_data="admin:back")],
    ])
    await edit_or_answer(query, "⚙️ <b>Настройки цен</b>", kb)
    await query.answer()


@dp.callback_query(F.data == "settings:universe_change_cost")
async def settings_universe_cost(query: types.CallbackQuery, state: FSMContext):
    if not admin_guard(query): await query.answer("Нет доступа", show_alert=True); return
    await state.set_state(SettingsAdmin.waiting_universe_change_cost)
    await edit_or_answer(query, "Введи новую цену смены вселенной в очках. Отмена: /cancel", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="fsm:cancel")]]))
    await query.answer()


@dp.message(SettingsAdmin.waiting_universe_change_cost)
async def settings_universe_cost_save(message: types.Message, state: FSMContext):
    if not admin_guard(message): return
    if not (message.text or "").strip().isdigit():
        await message.answer("Нужно число."); return
    value = int(message.text.strip())
    with closing(db()) as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('universe_change_cost', ?)", (str(value),))
    await state.clear()
    await message.answer(f"✅ Цена смены вселенной обновлена: <b>{value}</b> ⭐", parse_mode="HTML", reply_markup=admin_menu_keyboard())


@dp.callback_query(F.data == "admin:moderators")
async def admin_moderators(query: types.CallbackQuery, state: FSMContext):
    if not admin_guard(query): await query.answer("Нет доступа", show_alert=True); return
    await state.set_state(ModeratorAdmin.waiting_user)
    await edit_or_answer(query, "👮 Отправь ID, @тег или игровой ник пользователя, которому нужно выдать/забрать модератора. Отмена: /cancel", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="fsm:cancel")]]))
    await query.answer()


@dp.message(ModeratorAdmin.waiting_user)
async def admin_moderator_toggle(message: types.Message, state: FSMContext):
    if not admin_guard(message): return
    row = resolve_user_identifier(message.text or "")
    if not row:
        await message.answer("Пользователь не найден."); return
    new_role = "player" if (row["role"] == "moderator") else "moderator"
    with closing(db()) as conn:
        conn.execute("UPDATE users SET role=? WHERE id=?", (new_role, row["id"]))
    await state.clear()
    await message.answer(f"✅ Роль пользователя <b>{e(display_name(row))}</b>: <b>{'модератор' if new_role=='moderator' else 'игрок'}</b>", parse_mode="HTML", reply_markup=admin_menu_keyboard())


@dp.callback_query(F.data == "admin:seasons")
async def admin_seasons(query: types.CallbackQuery):
    if not admin_guard(query): await query.answer("Нет доступа", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="▶️ Начать сезон", callback_data="season:start")],
        [InlineKeyboardButton(text="⏹ Завершить сезон", callback_data="season:end_confirm")],
        [InlineKeyboardButton(text="⬅️ В админку", callback_data="admin:back")],
    ])
    await edit_or_answer(query, "🏁 <b>Сезоны</b>\n\nПри завершении сезона всем сбрасываются карты, очки, крутки, серия бонуса и выбранная вселенная. Перед сбросом бот сохранит топ-100 по очкам и отправит уведомление игрокам.", kb)
    await query.answer()


@dp.callback_query(F.data == "season:start")
async def season_start(query: types.CallbackQuery, state: FSMContext):
    if not admin_guard(query): await query.answer("Нет доступа", show_alert=True); return
    await state.set_state(SeasonAdmin.waiting_start_number)
    await edit_or_answer(query, "Введи номер нового сезона. Например: 1", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="fsm:cancel")]]))
    await query.answer()


@dp.message(SeasonAdmin.waiting_start_number)
async def season_start_save(message: types.Message, state: FSMContext):
    if not admin_guard(message): return
    if not (message.text or "").strip().isdigit():
        await message.answer("Нужен номер сезона."); return
    number = int(message.text.strip())
    with closing(db()) as conn:
        conn.execute("UPDATE seasons SET is_active=0, ended_at=COALESCE(ended_at, ?) WHERE is_active=1", (datetime.utcnow().isoformat(),))
        conn.execute("INSERT INTO seasons (number, is_active, started_at) VALUES (?, 1, ?)", (number, datetime.utcnow().isoformat()))
    await state.clear()
    await message.answer(f"✅ Сезон <b>{number}</b> начат.", parse_mode="HTML", reply_markup=admin_menu_keyboard())


@dp.callback_query(F.data == "season:end_confirm")
async def season_end_confirm(query: types.CallbackQuery):
    if not admin_guard(query): await query.answer("Нет доступа", show_alert=True); return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Да, завершить сезон", callback_data="season:end")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:seasons")],
    ])
    await edit_or_answer(query, "⚠️ Завершение сезона сбросит карты, очки, крутки, серию бонуса и выбранную вселенную у всех игроков. Подтвердить?", kb)
    await query.answer()


@dp.callback_query(F.data == "season:end")
async def season_end(query: types.CallbackQuery, state: FSMContext):
    if not admin_guard(query): await query.answer("Нет доступа", show_alert=True); return
    await state.set_state(SeasonAdmin.waiting_end_message)
    await edit_or_answer(query, "Отправь сообщение от администрации для уведомления о завершении сезона. Отмена: /cancel", InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Отмена", callback_data="fsm:cancel")]]))
    await query.answer()


@dp.message(SeasonAdmin.waiting_end_message)
async def season_end_save(message: types.Message, state: FSMContext):
    if not admin_guard(message): return
    admin_msg = (message.text or "Сезон завершён.").strip()
    with closing(db()) as conn:
        top = conn.execute("SELECT id, username, nickname, coins FROM users ORDER BY coins DESC, id ASC LIMIT 100").fetchall()
        users = [r["id"] for r in conn.execute("SELECT id FROM users").fetchall()]
        all_scores = conn.execute("SELECT id, coins FROM users").fetchall()
        season_rewards = {r["id"]: int(r["coins"] or 0) // SEASON_POINTS_PER_SPIN for r in all_scores}
        for place, row in enumerate(top[:3], 1):
            season_rewards[row["id"]] = season_rewards.get(row["id"], 0) + SEASON_TOP_SPIN_REWARDS.get(place, 0)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM cards WHERE is_limited=0")
        conn.execute("UPDATE users SET coins=0, drops=0, last_drop_at=NULL, spins=0, daily_streak=0, last_daily_at=NULL, active_universe_id=NULL")
        for reward_user_id, reward_spins in season_rewards.items():
            if reward_spins > 0:
                conn.execute("UPDATE users SET spins=? WHERE id=?", (reward_spins, reward_user_id))
        conn.execute("UPDATE seasons SET is_active=0, ended_at=?, end_message=? WHERE is_active=1", (datetime.utcnow().isoformat(), admin_msg))
        conn.commit()
    top_text_msg = "🏆 <b>Топ-100 сезона</b>\n" + ("\n".join(f"{i}. {e(display_name(r))} — ⭐ {r['coins']}" for i,r in enumerate(top,1)) if top else "Пока нет игроков.")
    notify = f"🏁 <b>Сезон завершён!</b>\n\n{e(admin_msg)}\n\n{top_text_msg}\n\nОбычные карты, очки и выбранная вселенная сброшены. Лимитки сохранены. Очки сезона сконвертированы в крутки, а топ-3 получил бонусы."
    sent = 0
    for uid in users:
        try:
            await bot.send_message(uid, notify, parse_mode="HTML")
            sent += 1
        except Exception:
            pass
    await state.clear()
    await message.answer(f"✅ Сезон завершён. Уведомления отправлены: <b>{sent}</b>", parse_mode="HTML", reply_markup=admin_menu_keyboard())

async def top_text(user_id: int) -> str:
    with closing(db()) as conn:
        universe = get_active_universe(conn, user_id)
        rows = conn.execute("""
            SELECT id, username, nickname, coins
            FROM users
            ORDER BY coins DESC, id ASC LIMIT 10
        """).fetchall()
    title = f"🏆 <b>Топ игроков</b>\n🌌 {e((universe['emoji'] + ' ' + universe['name']) if universe else 'вселенная не выбрана')}\n"
    if not rows:
        return "Пока нет игроков в рейтинге."
    return title + "\n" + "\n".join(f"{i}. <b>{e(display_name(r))}</b> — ⭐ {r['coins']}" for i, r in enumerate(rows, 1))


def top_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🏆 По очкам", callback_data="top:points"), InlineKeyboardButton(text="🔗 По рефералам", callback_data="top:refs")],
        [InlineKeyboardButton(text="⬅️ В меню", callback_data="menu:home")],
    ])


async def referral_top_text() -> str:
    with closing(db()) as conn:
        rows = conn.execute("""
            SELECT u.id, u.username, u.nickname, COUNT(r.id) AS refs
            FROM users u
            LEFT JOIN users r ON r.referrer_id = u.id
            GROUP BY u.id
            HAVING refs > 0
            ORDER BY refs DESC, u.id ASC
            LIMIT 10
        """).fetchall()
    if not rows:
        return "🔗 <b>Топ по рефералам</b>\n\nПока нет приглашённых игроков."
    return "🔗 <b>Топ по рефералам</b>\n\n" + "\n".join(f"{i}. <b>{e(display_name(r))}</b> — 👥 {r['refs']}" for i, r in enumerate(rows, 1))


@dp.message(Command("top"))
@dp.message(F.text == "🏆 Топ")
async def top(message: types.Message):
    ensure_user(message.from_user)
    await message.answer(await top_text(message.from_user.id), reply_markup=top_keyboard(), parse_mode="HTML")


@dp.callback_query(F.data == "menu:top")
async def top_button(query: types.CallbackQuery):
    ensure_user(query.from_user)
    await edit_or_answer(query, await top_text(query.from_user.id), top_keyboard())
    await query.answer()


@dp.callback_query(F.data == "top:points")
async def top_points_button(query: types.CallbackQuery):
    ensure_user(query.from_user)
    await edit_or_answer(query, await top_text(query.from_user.id), top_keyboard())
    await query.answer()


@dp.callback_query(F.data == "top:refs")
async def top_refs_button(query: types.CallbackQuery):
    ensure_user(query.from_user)
    await edit_or_answer(query, await referral_top_text(), top_keyboard())
    await query.answer()


@dp.message(F.new_chat_members)
async def bot_added_to_chat(message: types.Message):
    me = await bot.get_me()
    if not any(member.id == me.id for member in message.new_chat_members):
        return
    await message.answer(
        "Привет! Я HenCard: выдаю коллекционные карты, веду коллекции, магазин, топ и сезоны.\n\n"
        "Команды: /menu, /card, /daily, /collection, /shop, /profile, /top, /universe."
    )


@dp.errors()
async def errors_handler(event: types.ErrorEvent):
    logger.exception("Ошибка в обработчике", exc_info=event.exception)
    return True


@dp.message()
async def fallback(message: types.Message):
    ensure_user(message.from_user)
    if message.chat.type in {"group", "supergroup"}:
        await message.reply("Не понял команду. Используй /menu, /card, /daily, /collection, /shop, /profile, /top или /universe.")
    else:
        await message.answer("Не понял команду. Используй кнопки меню или /help.", reply_markup=main_menu_keyboard())


async def main():
    init_db()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
