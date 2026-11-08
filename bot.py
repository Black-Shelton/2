# -*- coding: utf-8 -*-
"""
Карточный бот. Один файл с логикой + game_data.py с данными карт. Без подпапок с кодом.
Запуск: python bot.py
Хостинг: Render.com, тип Web Service (см. render.yaml / инструкцию в чате).
"""
import glob
import html
import json
import mimetypes
import os
import random
import shutil
import signal
import socket as _socket
import sqlite3
import ssl
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import db_backup
from game_data import (CARDS, CASE_CARDS, CASES, EXCLUSIVE_CARDS, HALLOWEEN_CARDS,
                       find_card_by_name, get_income_cards)

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ======================================================================
# КОНФИГ
# ======================================================================
DB_PATH = os.path.join(BASE_DIR, "game.db")
IMG_DIR = BASE_DIR  # картинки карт лежат прямо рядом с bot.py, без подпапки


def _load_env(path):
    env = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return env


_env = _load_env(os.path.join(BASE_DIR, "setting.env"))


def _get(key, default=""):
    # Переменные окружения (Render Dashboard → Environment) имеют приоритет над setting.env
    return os.environ.get(key) or _env.get(key) or default


BOT_TOKEN = _get("BOT_TOKEN") or None

# Дефолтные админы, если ADMIN_IDS не задан через окружение/setting.env.
_DEFAULT_ADMIN_IDS = [7035855233, 7305299086, 5679778859]
_admin_env = _get("ADMIN_IDS")
if _admin_env:
    ADMIN_IDS = [int(x.strip()) for x in _admin_env.split(",") if x.strip().isdigit()]
else:
    ADMIN_IDS = list(_DEFAULT_ADMIN_IDS)

# Render сам выдаёт PORT для Web Service — обязателен, иначе сервис не поднимется.
PORT = int(_get("PORT", "10000"))

COOLDOWN = 3600               # КД призыва / эксперимента (1 час)
BONUS_COOLDOWN = 86400        # КД бонуса (24 часа)
BONUS_MIN_COINS = 100
BONUS_MAX_COINS = 500

COOLDOWN_PRIVILEGE = 1800
CARD_REWARD = 2
DUPLICATE_REWARD = 2
POINTS_PER_CARD = 1
POINTS_PER_LEVEL = 5
PRIVILEGE_COIN_MULT = 2.0
PRIVILEGE_LUCK_MULT = 1.5

MAX_INCOME_HOURS = 48

# Привилегии: priv_type в таблице privileges выбирает набор бонусов отсюда.
# "дуэли" в коде бота пока нет — coin_mult у leader уже заложен на будущее,
# применится сам, как только появится система дуэлей.
PRIVILEGES = {
    "vip": {
        "display": "VIP",
        "cooldown": COOLDOWN_PRIVILEGE,     # 30 мин вместо 60
        "coin_mult": PRIVILEGE_COIN_MULT,   # +100% монет за карты
        "luck_mult": PRIVILEGE_LUCK_MULT,   # +50% удача на некоторые карты
    },
    "leader": {
        "display": "Leader Of The Noobs",
        "cooldown": 45 * 60,                # 45 мин вместо 60
        "coin_mult": 1.15,                  # +15% монет за карты (и дуэли, когда появятся)
        "luck_mult": 1.10,                  # +10% удача на необычные карты
    },
}
DEFAULT_PRIV_TYPE = "vip"

# ======================================================================
# ЛОГГЕР — пишет в stdout (Render Dashboard → Logs), плюс в файл для локального запуска.
# В исходнике вывод в консоль был отключён — из-за этого при сбое старта (например,
# неверный BOT_TOKEN) в Pydroid/консоли ничего не было видно, только "[Program finished]".
# ======================================================================
LOG_PATH = os.path.join(BASE_DIR, "bot.log")


def log(*parts, exc=False):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(str(p) for p in parts)
    print(line, flush=True)
    if exc:
        traceback.print_exc()
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 1_000_000:
            os.replace(LOG_PATH, LOG_PATH + ".old")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            if exc:
                f.write(traceback.format_exc() + "\n")
    except Exception:
        pass


# ======================================================================
# БАЗА ДАННЫХ (sqlite)
# Render Free Web Service сам по себе не хранит диск между деплоями/рестартами/
# «засыпаниями» — game.db обнулялась бы. От этого защищает db_backup.py:
# при старте он тянет последнюю game.db из GitHub-репозитория (если локальной
# ещё нет), а пока бот работает — периодически коммитит её туда обратно.
# См. db_backup.py и README про переменные GITHUB_TOKEN / GITHUB_REPO.
# ======================================================================
_conn = None

PLAYER_COLS = {
    "username": "TEXT DEFAULT ''",
    "balance": "INTEGER DEFAULT 0",
    "last_card_time": "REAL DEFAULT 0",
    "last_bonus_time": "REAL DEFAULT 0",
    "points": "INTEGER DEFAULT 0",
    "level": "INTEGER DEFAULT 1",
    "active_income_card": "TEXT DEFAULT ''",
    "last_grim_time": "REAL DEFAULT 0",
    "candy": "INTEGER DEFAULT 0",
    "cards_received": "INTEGER DEFAULT 0",
}


def _db():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
    return _conn


def _ensure_columns(table, cols):
    have = {r["name"] for r in _db().execute("PRAGMA table_info(%s)" % table)}
    for name, ddl in cols.items():
        if name not in have:
            _db().execute("ALTER TABLE %s ADD COLUMN %s %s" % (table, name, ddl))


def init_db():
    if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0 and not glob.glob(DB_PATH + ".bak-*"):
        shutil.copy2(DB_PATH, DB_PATH + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))

    c = _db()
    c.execute("""CREATE TABLE IF NOT EXISTS players (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance INTEGER DEFAULT 0,
        last_card_time REAL DEFAULT 0,
        last_bonus_time REAL DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS inventory (
        user_id INTEGER,
        card_name TEXT,
        count INTEGER DEFAULT 1,
        PRIMARY KEY (user_id, card_name)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS crates (
        user_id INTEGER,
        case_id TEXT,
        count INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, case_id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS privileges (
        user_id INTEGER PRIMARY KEY,
        expires REAL DEFAULT 0
    )""")
    _ensure_columns("players", PLAYER_COLS)
    _ensure_columns("inventory", {"rarity": "TEXT DEFAULT ''"})
    _ensure_columns("privileges", {"priv_type": "TEXT DEFAULT 'vip'"})
    c.commit()

    # карта Grim переименована в Grim Ronin
    try:
        c.execute("UPDATE OR IGNORE inventory SET card_name = 'Grim Ronin' WHERE card_name = 'Grim'")
        c.execute("UPDATE players SET active_income_card = 'Grim Ronin' WHERE active_income_card = 'Grim'")
        c.commit()
    except Exception:
        pass

    # карта sword noob переименована в dual sword noob
    try:
        c.execute("UPDATE OR IGNORE inventory SET card_name = 'dual sword noob' WHERE card_name = 'sword noob'")
        c.execute("UPDATE players SET active_income_card = 'dual sword noob' WHERE active_income_card = 'sword noob'")
        c.commit()
    except Exception:
        pass

    # старые строки в privileges без priv_type считаем обычным VIP
    try:
        c.execute("UPDATE privileges SET priv_type = 'vip' WHERE priv_type IS NULL OR priv_type = ''")
        c.commit()
    except Exception:
        pass

    if c.execute("PRAGMA user_version").fetchone()[0] < 1:
        if os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
            shutil.copy2(DB_PATH, DB_PATH + ".bak-v1-" + time.strftime("%Y%m%d-%H%M%S"))
        c.execute("UPDATE players SET points = 0 WHERE points >= ?", (POINTS_PER_LEVEL,))
        c.execute("PRAGMA user_version = 1")
        c.commit()


def get_player(user_id, username=None):
    c = _db()
    row = c.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        c.execute("INSERT INTO players (user_id, username) VALUES (?, ?)", (user_id, username or ""))
        c.commit()
        row = c.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row)


def top_players(limit=10):
    rows = _db().execute(
        "SELECT username, balance, level FROM players ORDER BY balance DESC, level DESC, user_id LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in rows]


def update_username(user_id, username):
    c = _db()
    c.execute("UPDATE players SET username = ? WHERE user_id = ?", (username or "", user_id))
    c.commit()


def find_player_by_username(username):
    row = _db().execute(
        "SELECT * FROM players WHERE LOWER(username) = LOWER(?)", (username,)).fetchone()
    return dict(row) if row else None


def update_player(user_id, **fields):
    fields = {k: v for k, v in fields.items() if k in PLAYER_COLS}
    if not fields:
        return
    c = _db()
    sets = ", ".join("%s = ?" % k for k in fields)
    c.execute("UPDATE players SET %s WHERE user_id = ?" % sets, list(fields.values()) + [user_id])
    c.commit()


def add_balance(user_id, amount):
    c = _db()
    c.execute("UPDATE players SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
    c.commit()


def add_candy(user_id, amount):
    c = _db()
    c.execute("UPDATE players SET candy = MAX(0, COALESCE(candy, 0) + ?) WHERE user_id = ?", (amount, user_id))
    c.commit()


def set_last_card_time(user_id, timestamp):
    update_player(user_id, last_card_time=timestamp)


def set_last_bonus_time(user_id, timestamp):
    update_player(user_id, last_bonus_time=timestamp)


def get_last_grim_time(user_id):
    return get_player(user_id).get("last_grim_time") or 0


def set_last_grim_time(user_id, timestamp):
    update_player(user_id, last_grim_time=timestamp)


def get_active_income_card(user_id):
    return get_player(user_id).get("active_income_card") or ""


def set_active_income_card(user_id, card_name):
    update_player(user_id, active_income_card=card_name or "")


def reset_player(user_id):
    c = _db()
    c.execute(
        "UPDATE players SET balance = 0, last_card_time = 0, last_bonus_time = 0, points = 0, "
        "level = 1, active_income_card = '', last_grim_time = 0, candy = 0 WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM inventory WHERE user_id = ?", (user_id,))
    c.execute("DELETE FROM crates WHERE user_id = ?", (user_id,))
    c.commit()


def add_to_inventory(user_id, card_name, rarity=None):
    if not rarity:
        info = find_card_by_name(card_name)
        rarity = info["rarity"] if info else ""
    c = _db()
    c.execute("UPDATE players SET cards_received = COALESCE(cards_received, 0) + 1 WHERE user_id = ?", (user_id,))
    row = c.execute(
        "SELECT count FROM inventory WHERE user_id = ? AND card_name = ?", (user_id, card_name)).fetchone()
    if row:
        c.execute("UPDATE inventory SET count = count + 1 WHERE user_id = ? AND card_name = ?",
                  (user_id, card_name))
        c.commit()
        return False
    c.execute("INSERT INTO inventory (user_id, card_name, count, rarity) VALUES (?, ?, 1, ?)",
              (user_id, card_name, rarity))
    c.commit()
    return True


def get_card_count(user_id, card_name):
    row = _db().execute(
        "SELECT count FROM inventory WHERE user_id = ? AND card_name = ?", (user_id, card_name)).fetchone()
    return row["count"] if row else 0


def remove_from_inventory(user_id, card_name, amount=1):
    c = _db()
    for _ in range(max(1, amount)):
        row = c.execute(
            "SELECT count FROM inventory WHERE user_id = ? AND card_name = ?", (user_id, card_name)).fetchone()
        if not row:
            break
        if row["count"] > 1:
            c.execute("UPDATE inventory SET count = count - 1 WHERE user_id = ? AND card_name = ?",
                      (user_id, card_name))
        else:
            c.execute("DELETE FROM inventory WHERE user_id = ? AND card_name = ?", (user_id, card_name))
    c.commit()


def get_inventory(user_id):
    rows = _db().execute(
        "SELECT * FROM inventory WHERE user_id = ? AND count > 0 ORDER BY card_name", (user_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if not d.get("rarity"):
            info = find_card_by_name(d["card_name"])
            d["rarity"] = info["rarity"] if info else "?"
        out.append(d)
    return out


def add_crate(user_id, case_id, amount=1):
    c = _db()
    c.execute(
        "INSERT INTO crates (user_id, case_id, count) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id, case_id) DO UPDATE SET count = count + ?",
        (user_id, case_id, amount, amount))
    c.commit()


def get_crate_count(user_id, case_id):
    row = _db().execute(
        "SELECT count FROM crates WHERE user_id = ? AND case_id = ?", (user_id, case_id)).fetchone()
    return row["count"] if row else 0


def get_crates(user_id):
    rows = _db().execute(
        "SELECT case_id, count FROM crates WHERE user_id = ? AND count > 0", (user_id,)).fetchall()
    return [dict(r) for r in rows]


def use_crate(user_id, case_id):
    if get_crate_count(user_id, case_id) <= 0:
        return False
    c = _db()
    c.execute("UPDATE crates SET count = count - 1 WHERE user_id = ? AND case_id = ?", (user_id, case_id))
    c.commit()
    return True


def get_privilege(user_id):
    row = _db().execute("SELECT * FROM privileges WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return None
    exp = row["expires"] or 0
    if exp and exp < time.time():
        return None
    d = dict(row)
    d["priv_type"] = d.get("priv_type") or DEFAULT_PRIV_TYPE
    return d


def set_privilege(user_id, priv_type, expires):
    """expires = 0 — привилегия навсегда."""
    c = _db()
    c.execute(
        "INSERT INTO privileges (user_id, expires, priv_type) VALUES (?, ?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET expires = excluded.expires, priv_type = excluded.priv_type",
        (user_id, expires, priv_type))
    c.commit()


def get_stats():
    row = _db().execute(
        "SELECT COUNT(*) AS n, COALESCE(SUM(balance),0) AS coins, COALESCE(SUM(level),0) AS levels, "
        "COALESCE(SUM(points),0) AS points, COALESCE(SUM(cards_received),0) AS cards FROM players").fetchone()
    return dict(row)


# ======================================================================
# TELEGRAM-КЛИЕНТ — чистый stdlib (urllib), ничего через pip ставить не нужно.
# ======================================================================
class TelegramError(Exception):
    def __init__(self, description, code=0, retry_after=0):
        super().__init__(description)
        self.description = description
        self.code = code
        self.retry_after = retry_after


def _multipart(fields, files):
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(
            ('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v)).encode("utf-8")
        )
    for k, path in files.items():
        fname = os.path.basename(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            content = f.read()
        head = (
            '--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n'
            % (boundary, k, fname, ctype)
        ).encode("utf-8")
        parts.append(head + content + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return b"".join(parts), "multipart/form-data; boundary=%s" % boundary


class Bot:
    def __init__(self, token):
        self.base = "https://api.telegram.org/bot%s/" % token

    def call(self, method, params=None, files=None, timeout=60, _retry=True):
        params = {k: v for k, v in (params or {}).items() if v is not None}
        if files:
            fields = {}
            for k, v in params.items():
                if isinstance(v, (dict, list)):
                    v = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, bool):
                    v = "true" if v else "false"
                fields[k] = v
            body, ctype = _multipart(fields, files)
            req = urllib.request.Request(self.base + method, data=body, headers={"Content-Type": ctype})
        else:
            body = json.dumps(params, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self.base + method, data=body, headers={"Content-Type": "application/json"}
            )
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=_CTX) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                raise TelegramError("HTTP %s" % e.code, code=e.code)
        if not data.get("ok"):
            retry_after = (data.get("parameters") or {}).get("retry_after", 0)
            if data.get("error_code") == 429 and retry_after and _retry:
                time.sleep(retry_after + 1)
                return self.call(method, params, files, timeout, _retry=False)
            raise TelegramError(data.get("description", "error"), data.get("error_code", 0), retry_after)
        return data["result"]


class KB:
    """Аналог InlineKeyboardBuilder: button / adjust / as_markup."""

    def __init__(self):
        self.buttons = []
        self.sizes = None

    def button(self, text, callback_data):
        self.buttons.append({"text": text, "callback_data": callback_data})

    def adjust(self, *sizes):
        self.sizes = sizes

    def as_markup(self):
        sizes = list(self.sizes) if self.sizes else [8]
        rows, i, si = [], 0, 0
        while i < len(self.buttons):
            size = sizes[si] if si < len(sizes) else sizes[-1]
            rows.append(self.buttons[i:i + size])
            i += size
            si += 1
        return {"inline_keyboard": rows}


class User:
    def __init__(self, raw):
        self.id = raw["id"]
        self.username = raw.get("username")
        self.first_name = raw.get("first_name") or ""


class Msg:
    def __init__(self, bot, raw):
        self.bot = bot
        self.raw = raw
        self.chat_id = raw["chat"]["id"]
        self.chat_type = raw["chat"].get("type")
        self.message_id = raw["message_id"]
        self.text = raw.get("text")
        self.from_user = User(raw["from"]) if raw.get("from") else None
        r = raw.get("reply_to_message")
        self.reply_to_message = Msg(bot, r) if r and "chat" in r else None
        self._thread = raw.get("message_thread_id") if raw.get("is_topic_message") else None

    @property
    def is_forwarded(self):
        return any(k in self.raw for k in (
            "forward_origin", "forward_from", "forward_from_chat", "forward_date", "forward_sender_name"))

    @property
    def forwarded_from_channel(self):
        fc = self.raw.get("forward_from_chat")
        if fc and fc.get("type") == "channel":
            return True
        fo = self.raw.get("forward_origin") or {}
        return fo.get("type") == "channel"

    def _base(self, reply):
        p = {"chat_id": self.chat_id, "message_thread_id": self._thread}
        if reply:
            p["reply_parameters"] = {"message_id": self.message_id, "allow_sending_without_reply": True}
        return p

    def _text(self, text, parse_mode, reply_markup, reply, no_preview=False):
        p = self._base(reply)
        p.update({"text": text, "parse_mode": parse_mode, "reply_markup": reply_markup})
        if no_preview:
            p["link_preview_options"] = {"is_disabled": True}
        return self.bot.call("sendMessage", p)

    def _media(self, kind, path, caption, parse_mode, reply):
        p = self._base(reply)
        p.update({"caption": caption, "parse_mode": parse_mode})
        return self.bot.call("send" + kind.capitalize(), p, files={kind: path}, timeout=120)

    def answer(self, text, parse_mode=None, reply_markup=None, no_preview=False):
        return self._text(text, parse_mode, reply_markup, False, no_preview)

    def reply(self, text, parse_mode=None, reply_markup=None):
        return self._text(text, parse_mode, reply_markup, True)

    def answer_photo(self, path, caption=None, parse_mode=None):
        return self._media("photo", path, caption, parse_mode, False)

    def reply_photo(self, path, caption=None, parse_mode=None):
        return self._media("photo", path, caption, parse_mode, True)

    def reply_video(self, path, caption=None, parse_mode=None):
        return self._media("video", path, caption, parse_mode, True)

    def reply_document(self, path, caption=None, parse_mode=None):
        return self._media("document", path, caption, parse_mode, True)

    def answer_document(self, path, caption=None, parse_mode=None):
        return self._media("document", path, caption, parse_mode, False)

    def edit_text(self, text, reply_markup=None, parse_mode=None):
        try:
            return self.bot.call("editMessageText", {
                "chat_id": self.chat_id, "message_id": self.message_id, "text": text,
                "parse_mode": parse_mode, "reply_markup": reply_markup})
        except TelegramError as e:
            if "not modified" in e.description:
                return None
            raise

    def delete(self):
        try:
            self.bot.call("deleteMessage", {"chat_id": self.chat_id, "message_id": self.message_id})
        except TelegramError:
            pass


class Callback:
    def __init__(self, bot, raw):
        self.bot = bot
        self.id = raw["id"]
        self.data = raw.get("data") or ""
        self.from_user = User(raw["from"])
        m = raw.get("message")
        self.message = Msg(bot, m) if m and "chat" in m else None

    def answer(self, text=None, show_alert=False):
        try:
            self.bot.call("answerCallbackQuery", {
                "callback_query_id": self.id, "text": text, "show_alert": True if show_alert else None})
        except TelegramError:
            pass


# ======================================================================
# РЕЕСТР ХЕНДЛЕРОВ
# ======================================================================
MESSAGE_HANDLERS = []
CALLBACK_HANDLERS = []


def on_message(pred):
    def deco(fn):
        MESSAGE_HANDLERS.append((pred, fn))
        return fn
    return deco


def on_callback(prefix):
    def deco(fn):
        CALLBACK_HANDLERS.append((prefix, fn))
        return fn
    return deco


def text_is(word, allow_forward=True):
    def pred(m):
        if m.text is None or m.text.lower() != word:
            return False
        return allow_forward or not m.is_forwarded
    return pred


def text_starts(prefix):
    return lambda m: m.text is not None and m.text.lower().startswith(prefix)


def is_admin(user_id):
    return user_id in ADMIN_IDS


# ======================================================================
# КЛАВИАТУРЫ
# ======================================================================
def main_menu(user_id: int = 0):
    kb = KB()
    kb.button(text="🃏 Карты",   callback_data="menu_cards")
    kb.button(text="👤 Профиль", callback_data="menu_profile")
    kb.button(text="🏆 Топ",     callback_data="menu_top")
    kb.button(text="🎁 Бонус",   callback_data="menu_bonus")
    kb.button(text="📦 Кейсы",   callback_data="menu_cases")
    kb.button(text="📜 Квесты",  callback_data="menu_quests")
    kb.button(text="⚒️ Крафты",  callback_data="menu_crafts")
    kb.button(text="💎 Донат",   callback_data="menu_donate")
    kb.adjust(2)
    return kb.as_markup()


def cards_menu(user_id: int):
    kb = KB()
    kb.button(text="📚 Все карты",     callback_data=f"cards_all_{user_id}")
    kb.button(text="🎒 Мой инвентарь", callback_data=f"cards_inv_{user_id}")
    kb.button(text="⬅️ Назад",          callback_data=f"menu_back_{user_id}")
    kb.adjust(1)
    return kb.as_markup()


def back_menu(user_id: int):
    kb = KB()
    kb.button(text="⬅️ Назад", callback_data=f"menu_back_{user_id}")
    return kb.as_markup()


def case_actions_menu(case_id: str, user_id: int):
    kb = KB()
    kb.button(text="🛒 Купить", callback_data=f"case_buy_{case_id}_{user_id}")
    kb.button(text="🎴 Карты",  callback_data=f"case_cards_{case_id}_{user_id}")
    kb.button(text="⬅️ Назад",  callback_data=f"menu_back_{user_id}")
    kb.adjust(2, 1)
    return kb.as_markup()


def check_owner(callback_data: str, user_id: int) -> bool:
    try:
        owner_id = int(callback_data.split("_")[-1])
    except (ValueError, IndexError):
        return False
    return user_id == owner_id


# ======================================================================
# СТАРТ / МЕНЮ / ТОП
# ======================================================================
START_TEXT = (
    "Привет ребятки, этот бот был создан для развлечения и для фанатов экспериментов.\n"
    "Веселитесь/радуйтесь.\n\n"
    "Также наши сообщества где вы можете предложить свою идею или карту:\n\n"
    "Тгк: https://t.me/Genes1sChannel\n\n"
    "Тгч: https://t.me/G3n3sisChat\n\n"
    "Спасибо за внимание"
)

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def top_text():
    lines = ["<b>🏆 Топ игроков по монетам:</b>"]
    for i, r in enumerate(top_players(10), 1):
        mark = MEDALS.get(i, f"{i}.")
        name = html.escape(r["username"] or "Игрок", quote=False)
        lines.append(f"<b>{mark} {name} — {r['balance']} 🪙 (ур. {r['level']})</b>")
    return "\n".join(lines)


@on_message(text_is("/start"))
def cmd_start(message):
    message.answer(START_TEXT, reply_markup=main_menu(message.from_user.id), no_preview=True)


@on_message(text_is("меню"))
def cmd_menu(message):
    message.answer("Меню", reply_markup=main_menu(message.from_user.id))


@on_message(text_is("топ"))
def cmd_top(message):
    message.answer(top_text(), parse_mode="HTML")


@on_callback("menu_profile")
def cb_profile(callback):
    callback.answer()
    profile_for(callback.from_user, callback.message, reply=False)


@on_callback("menu_top")
def cb_top(callback):
    callback.answer()
    callback.message.answer(top_text(), parse_mode="HTML")


@on_callback("menu_bonus")
def cb_bonus(callback):
    callback.answer()
    bonus_for(callback.from_user, callback.message, reply=False)


@on_callback("menu_cases")
def cb_cases(callback):
    callback.answer()
    show_case_shop(callback.message, callback.from_user.id)


QUESTS_TEXT = (
    "Мабой квесты появятся скоро, смотри новости бота по этой ссылке:\n\n"
    "https://t.me/Genes1sChannel"
)


@on_callback("menu_quests")
def cb_quests(callback):
    callback.answer()
    callback.message.answer(QUESTS_TEXT, no_preview=False)


@on_message(text_is("квесты"))
def cmd_quests(message):
    message.answer(QUESTS_TEXT, no_preview=False)


CRAFTS_TEXT = (
    "Крч крафты скоро появятся, так что жди\n"
    "И подпишись на канал:\n\n"
    "https://t.me/Genes1sChannel"
)


@on_callback("menu_crafts")
def cb_crafts(callback):
    callback.answer()
    callback.message.answer(CRAFTS_TEXT, no_preview=False)


@on_message(text_is("крафты"))
def cmd_crafts(message):
    message.answer(CRAFTS_TEXT, no_preview=False)


@on_callback("menu_back_")
def menu_back(callback):
    if not check_owner(callback.data, callback.from_user.id):
        callback.answer("❌ Это меню не для тебя.", show_alert=True)
        return
    callback.answer()
    if callback.message:
        callback.message.delete()


# ======================================================================
# БОНУС / ПРОФИЛЬ
# ======================================================================
def bonus_for(user, target, reply=True):
    send = target.reply if reply else target.answer
    user_id = user.id
    username = user.username or user.first_name
    player = get_player(user_id, username)
    update_username(user_id, username)

    now = time.time()
    if now - player["last_bonus_time"] < BONUS_COOLDOWN:
        rem = int(BONUS_COOLDOWN - (now - player["last_bonus_time"]))
        send(f"⏳ Бонус будет доступен через {rem // 3600}ч {(rem % 3600) // 60}м")
        return

    coins = random.randint(BONUS_MIN_COINS, BONUS_MAX_COINS)
    add_balance(user_id, coins)
    set_last_bonus_time(user_id, now)

    new_bal = player["balance"] + coins
    send(f"🎁 <b>Бонус!</b>\n\n+{coins} 🪙\nБаланс: <b>{new_bal}</b> 🪙", parse_mode="HTML")


@on_message(text_is("бонус"))
def cmd_bonus(message):
    bonus_for(message.from_user, message, reply=True)


def profile_for(user, target, reply=False):
    send = target.reply if reply else target.answer
    username = user.username or user.first_name
    player = get_player(user.id, username)
    update_username(user.id, username)
    priv = get_privilege(user.id)
    status = PRIVILEGES.get(priv["priv_type"], PRIVILEGES[DEFAULT_PRIV_TYPE])["display"] if priv else "Игрок"
    name = html.escape(username or "", quote=False)
    send(
        "👤 <b>Профиль</b>\n\n"
        f"Имя: {name}.\n"
        f"Монеты: {player['balance']} 🪙.\n"
        f"🍬 Candy: {player.get('candy') or 0}\n"
        f"Уровень: {player['level']}.\n"
        f"Очки: {player['points']}/{POINTS_PER_LEVEL}.\n"
        f"👑 Привилегия: {status}.",
        parse_mode="HTML",
    )


@on_message(text_is("профиль"))
def cmd_profile(message):
    profile_for(message.from_user, message, reply=False)


# ======================================================================
# ПРИЗЫВ (упрощённая выдача карты — отдельная от "эксперимент")
# ======================================================================
@on_message(text_is("призыв", allow_forward=False))
def trigger_card_simple(message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    player = get_player(user_id, username)
    update_username(user_id, username)

    now = time.time()
    if now - player["last_card_time"] < COOLDOWN:
        rem = int(COOLDOWN - (now - player["last_card_time"]))
        message.reply(f"⏳ Подожди {rem // 60}м {rem % 60}с")
        return

    pool = [c for c in CARDS if c["chance"] > 0]  # шанс 0% — только выдача админом
    total_chance = sum(c["chance"] for c in pool)
    roll = random.uniform(0, total_chance)
    current = 0
    card = pool[-1]
    for c in pool:
        current += c["chance"]
        if roll <= current:
            card = c
            break

    is_new = add_to_inventory(user_id, card["name"])
    total_count = get_card_count(user_id, card["name"])
    set_last_card_time(user_id, now)

    status = "🆕 Новая карта!" if is_new else "♻️ Дубликат"
    caption = f"{status}\n\n🎴 Карта: <b>{card['name']}</b>\nУ тебя: <b>×{total_count}</b>"

    media_path = os.path.join(IMG_DIR, card.get("file", ""))
    if card.get("file") and os.path.exists(media_path):
        message.reply_photo(media_path, caption=caption, parse_mode="HTML")
    else:
        message.reply(caption, parse_mode="HTML")


# ======================================================================
# ЭКСПЕРИМЕНТ / КАРТЫ / ИНВЕНТАРЬ / КЕЙСЫ / БАЗА
# ======================================================================
_RARITY_ORDER = ["Обычная", "Редкая", "Эпическая", "Мифическая", "Легендарная", "Секретная", "Эксклюзивная", "Halloween"]


def _power_hp(card):
    """«1200, хп 1250» для карт с хп, иначе просто сила."""
    if card.get("hp"):
        return f"{card['power']}, хп {card['hp']}"
    return f"{card['power']}"


def _split_text(text, limit=3900):
    """Режет длинный текст по строкам под лимит Telegram."""
    parts, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > limit and cur:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    return parts


def _build_card_index():
    lines = ["<b>Все карты в боте</b>\n"]
    n = 1
    for rarity in _RARITY_ORDER:
        for c in CARDS + CASE_CARDS:
            if c["rarity"] != rarity:
                continue
            lines.append(f"{n}. <b>{c['name']}</b> | {rarity} | сила {_power_hp(c)} | шанс {c['chance']}%")
            n += 1
    return "\n".join(lines)


def get_random_card(luck_mult=1.0):
    cards = []
    for c in CARDS:
        if c["chance"] <= 0:  # шанс 0% — только выдача админом
            continue
        chance = c["chance"]
        if luck_mult != 1.0 and c["rarity"] != "Обычная":
            chance *= luck_mult
        cards.append({**c, "chance": chance})
    total = sum(c["chance"] for c in cards)
    roll = random.uniform(0, total)
    current = 0
    for card in cards:
        current += card["chance"]
        if roll <= current:
            return card
    return cards[-1]


def get_random_case_card(case_id):
    case = CASES.get(case_id)
    if not case:
        return None
    cards = case["cards"]
    if not cards:
        return None
    total = sum(c["chance"] for c in cards)
    roll = random.uniform(0, total)
    current = 0
    for card in cards:
        current += card["chance"]
        if roll <= current:
            return card
    return cards[-1]


def _show(message_obj, text, markup, edit):
    if edit:
        try:
            message_obj.edit_text(text, reply_markup=markup, parse_mode="HTML")
            return
        except TelegramError:
            pass
    message_obj.answer(text, reply_markup=markup, parse_mode="HTML")


def show_base(message_obj, user_id, edit=False):
    inv = get_inventory(user_id)
    income_names = [c["name"] for c in get_income_cards()]
    player_income = [c for c in inv if c["card_name"] in income_names]

    active_check = get_active_income_card(user_id)
    if active_check and not any(c["card_name"] == active_check for c in inv):
        set_active_income_card(user_id, "")

    if not player_income:
        text = "❌ У тебя нет карт с доходом."
        if edit:
            try:
                message_obj.edit_text(text)
            except TelegramError:
                message_obj.answer(text)
        else:
            message_obj.answer(text)
        return

    active = get_active_income_card(user_id)
    text = "🏠 <b>База — карты дохода</b>\n\n"
    text += f"Экипировано: <b>{active if active else 'ничего'}</b>\n"
    text += "<i>(можно только 1 карту дохода)</i>\n\n"
    text += "<b>Твои карты дохода:</b>\n"

    kb = KB()
    for c in player_income:
        card_info = find_card_by_name(c["card_name"])
        if c["card_name"] == "Grim Ronin":
            income_text = "особый (70% +1500 / 30% −1999 за 2ч)"
        else:
            income = card_info.get("income", 0) if card_info else 0
            income_text = f"{income} 🪙/час"

        cnt = c.get("count", 1)
        cnt_text = f" ×{cnt}" if cnt > 1 else ""

        if c["card_name"] == active:
            text += f"✅ <b>{c['card_name']}</b>{cnt_text} — {income_text}\n"
        else:
            text += f"• <b>{c['card_name']}</b>{cnt_text} — {income_text}\n"
            kb.button(text=f"🔷 Экипировать {c['card_name']}", callback_data=f"base_equip_{c['card_name']}_{user_id}")

    if active:
        kb.button(text=f"❌ Снять {active}", callback_data=f"base_unequip_{user_id}")

    text += "\nНапиши <b>база собрать</b>, чтобы забрать доход."
    text += "\n<i>⚠️ При смене карты накопленное сгорает!</i>"
    kb.adjust(1)

    _show(message_obj, text, kb.as_markup(), edit)


def show_my_crates(message_obj, user_id, edit=False):
    crates = get_crates(user_id)
    if not crates:
        text = "📦 <b>Мои кейсы</b>\n\n❌ У тебя нет кейсов.\n\nКупи их: команда <b>кейс</b>"
        kb = KB()
        kb.button(text="⬅️ Назад", callback_data=f"menu_back_{user_id}")
        _show(message_obj, text, kb.as_markup(), edit)
        return

    text = "📦 <b>Мои кейсы</b>\n\n"
    kb = KB()
    for c in crates:
        case_id = c["case_id"]
        count = c["count"]
        case = CASES.get(case_id)
        if not case:
            continue
        text += f"• <b>{case['name']}</b> — ×{count}\n"
        kb.button(text=f"📦 {case['name']} ({count})", callback_data=f"mycrate_{case_id}_{user_id}")
    kb.button(text="⬅️ Назад", callback_data=f"menu_back_{user_id}")
    kb.adjust(1)

    _show(message_obj, text, kb.as_markup(), edit)


def _inventory_view(user_id, empty_text):
    inv = get_inventory(user_id)
    text = "🎒 <b>Инвентарь</b>\n\n"
    kb = KB()
    if inv:
        for c in inv:
            card = find_card_by_name(c["card_name"])
            price = card["sell"] if card else 0
            power = _power_hp(card) if card else "?"
            cnt = c.get("count", 1)
            cnt_text = f" ×{cnt}" if cnt > 1 else ""
            text += f"• <b>{c['card_name']}</b>{cnt_text} — {c['rarity']} (сила {power}, продажа {price} 🪙)\n"
            kb.button(text=f"💰 Продать {c['card_name']}", callback_data=f"sell_{c['card_name']}_{user_id}")
    else:
        text += empty_text
    text += "\n📦 Кейсы: команда <b>мои кейсы</b>"
    kb.button(text="⬅️ Назад", callback_data=f"menu_back_{user_id}")
    kb.adjust(1)
    return text, kb.as_markup()


@on_message(text_is("эксперимент", allow_forward=False))
def trigger_card(message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    player = get_player(user_id, username)
    update_username(user_id, username)
    now = time.time()

    priv = get_privilege(user_id)
    has_priv = bool(priv)
    priv_conf = PRIVILEGES.get(priv["priv_type"], PRIVILEGES[DEFAULT_PRIV_TYPE]) if has_priv else None
    cooldown = priv_conf["cooldown"] if priv_conf else COOLDOWN

    elapsed = now - player["last_card_time"]
    if elapsed < cooldown:
        remaining = int(cooldown - elapsed)
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        secs = remaining % 60
        message.reply(f"⏳ Подожди ещё <b>{hours}ч {minutes}м {secs}с</b>", parse_mode="HTML")
        return

    card = get_random_card(luck_mult=priv_conf["luck_mult"] if priv_conf else 1.0)
    is_new = add_to_inventory(user_id, card["name"], card["rarity"])

    multiplier = priv_conf["coin_mult"] if priv_conf else 1.0
    if is_new:
        reward = int(CARD_REWARD * multiplier)
        status = "🆕 Новая карта!"
    else:
        reward = int(DUPLICATE_REWARD * multiplier)
        status = "♻️ Дубликат (+1 к количеству)"

    new_balance = player["balance"] + reward
    new_points = player["points"] + POINTS_PER_CARD
    new_level = player["level"]
    level_up = False

    while new_points >= POINTS_PER_LEVEL:
        new_level += 1
        new_points -= POINTS_PER_LEVEL
        level_up = True

    update_player(user_id, balance=new_balance, points=new_points, level=new_level, last_card_time=now)

    total_count = get_card_count(user_id, card["name"])

    priv_line = f"\n👑 Привилегия: {priv_conf['display']}" if has_priv else ""
    caption = (
        f"{status}\n\n"
        f"🎴 Карта: <b>{card['name']}</b>\n"
        f"Редкость: <b>{card['rarity']}</b>\n"
        f"Сила: <b>{_power_hp(card)}</b>\n"
        f"У тебя: <b>×{total_count}</b>\n"
        f"Награда: +{reward} 🪙\n"
        f"Баланс: <b>{new_balance}</b> 🪙\n"
        f"Уровень: <b>{new_level}</b> | Очки: {new_points}/{POINTS_PER_LEVEL}"
        f"{priv_line}"
    )
    if level_up:
        caption += f"\n\n🎉 <b>Новый уровень {new_level}!</b>"

    media_path = os.path.join(IMG_DIR, card["file"])
    if os.path.exists(media_path):
        ext = card["file"].lower().rsplit(".", 1)[-1]
        if ext in ("mp4", "webm", "mov"):
            message.reply_video(media_path, caption=caption, parse_mode="HTML")
        else:
            message.reply_photo(media_path, caption=caption, parse_mode="HTML")
    else:
        message.reply(caption, parse_mode="HTML")


@on_callback("menu_cards")
def cb_card_index(callback):
    callback.answer()
    user_id = callback.from_user.id
    text = _build_card_index()
    kb = KB()
    kb.button(text="⬅️ Назад", callback_data=f"menu_back_{user_id}")
    try:
        callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    except TelegramError:
        callback.message.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@on_callback("cards_")
def process_cards(callback):
    if not check_owner(callback.data, callback.from_user.id):
        callback.answer("❌ Это меню не для тебя.", show_alert=True)
        return
    callback.answer()
    action = callback.data.rsplit("_", 1)[0]
    user_id = callback.from_user.id

    if action == "cards_all":
        def _line(card):
            income = f" | доход {card['income']}/ч" if card.get("income", 0) > 0 else ""
            return f"• <b>{card['name']}</b> — {card['rarity']} (сила {_power_hp(card)}, шанс {card['chance']}%{income})\n"

        text = "📚 <b>Все карты</b>\n\n"
        for card in CARDS:
            text += _line(card)
        text += "\n<b>Эксклюзивные:</b>\n"
        for card in EXCLUSIVE_CARDS:
            text += _line(card)
        text += "\n<b>Хеллоуин:</b>\n"
        for card in HALLOWEEN_CARDS:
            text += _line(card)
        chunks = _split_text(text)
        for chunk in chunks[:-1]:
            callback.message.answer(chunk, parse_mode="HTML")
        text = chunks[-1]
        callback.message.edit_text(text, reply_markup=cards_menu(user_id), parse_mode="HTML")

    elif action == "cards_inv":
        text, markup = _inventory_view(user_id, "Пусто. Напиши «эксперимент», чтобы получить карту!\n")
        callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@on_callback("sell_")
def sell_card(callback):
    parts = callback.data.split("_")
    owner_id = int(parts[-1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твой инвентарь.", show_alert=True)
        return
    card_name = "_".join(parts[1:-1])
    card = find_card_by_name(card_name)
    if not card:
        callback.answer("Карта не найдена", show_alert=True)
        return

    count = get_card_count(callback.from_user.id, card_name)
    if count <= 0:
        callback.answer("У тебя нет этой карты", show_alert=True)
        return

    player = get_player(callback.from_user.id)
    remove_from_inventory(callback.from_user.id, card_name, 1)
    new_balance = player["balance"] + card["sell"]
    update_player(callback.from_user.id, balance=new_balance)

    active = get_active_income_card(callback.from_user.id)
    if active == card_name:
        remaining = get_card_count(callback.from_user.id, card_name)
        if remaining <= 0:
            set_active_income_card(callback.from_user.id, "")

    callback.answer(f"Продано за {card['sell']} 🪙 (осталось: {count - 1})", show_alert=True)

    text, markup = _inventory_view(callback.from_user.id, "Пусто.\n")
    callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")


@on_message(text_is("инвентарь"))
@on_message(text_is("инв"))
def cmd_inventory(message):
    user_id = message.from_user.id
    get_player(user_id, message.from_user.username)
    update_username(user_id, message.from_user.username)
    text, markup = _inventory_view(user_id, "Пусто. Напиши «эксперимент», чтобы получить карту!\n")
    message.answer(text, reply_markup=markup, parse_mode="HTML")


@on_message(text_is("мои кейсы"))
def cmd_my_crates(message):
    user_id = message.from_user.id
    get_player(user_id, message.from_user.username)
    update_username(user_id, message.from_user.username)
    show_my_crates(message, user_id, edit=False)


@on_callback("mycrate_")
def mycrate_view(callback):
    parts = callback.data.split("_")
    case_id = "_".join(parts[1:-1])
    owner_id = int(parts[-1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return
    callback.answer()

    case = CASES.get(case_id)
    if not case:
        callback.answer("Кейс не найден", show_alert=True)
        return

    count = get_crate_count(owner_id, case_id)
    if count <= 0:
        callback.answer("У тебя нет этого кейса", show_alert=True)
        return

    text = f"📦 <b>{case['name']}</b>\n\nУ тебя: <b>{count}</b> шт.\n\nНажми «Открыть»."
    kb = KB()
    kb.button(text="🎁 Открыть", callback_data=f"mycrateopen_{case_id}_{owner_id}")
    kb.button(text="⬅️ Назад", callback_data=f"mycrates_{owner_id}")
    kb.adjust(1)
    try:
        callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")
    except TelegramError:
        pass


@on_callback("mycrates_")
def mycrates_back(callback):
    owner_id = int(callback.data.split("_")[1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return
    callback.answer()
    show_my_crates(callback.message, owner_id, edit=True)


@on_callback("mycrateopen_")
def mycrate_do_open(callback):
    parts = callback.data.split("_")
    case_id = "_".join(parts[1:-1])
    owner_id = int(parts[-1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return

    case = CASES.get(case_id)
    if not case:
        callback.answer("Кейс не найден", show_alert=True)
        return

    if not case.get("cards"):
        callback.answer("❌ В кейсе пока нет карт", show_alert=True)
        return

    used = use_crate(owner_id, case_id)
    if not used:
        callback.answer("Кейсов больше нет", show_alert=True)
        return

    user_id = callback.from_user.id
    get_player(user_id, callback.from_user.username)

    card = get_random_case_card(case_id)
    if not card:
        callback.answer("Ошибка кейса", show_alert=True)
        return

    is_new = add_to_inventory(user_id, card["name"], card["rarity"])
    if is_new:
        status = "🆕 Новая карта!"
    else:
        status = "♻️ Дубликат (+2 🪙)"
        add_balance(user_id, 2)

    new_count = get_crate_count(user_id, case_id)
    total_card_count = get_card_count(user_id, card["name"])

    callback.answer("📦 Кейс открыт!", show_alert=False)

    caption = (
        f"📦 <b>Кейс открыт!</b>\n\n{status}\n\n"
        f"🎴 Карта: <b>{card['name']}</b>\n"
        f"Редкость: <b>{card['rarity']}</b>\n"
        f"Сила: <b>{_power_hp(card)}</b>\n"
        f"У тебя: <b>×{total_card_count}</b>\n"
        f"Шанс: {card['chance']}%\n\n"
        f"📦 Осталось: <b>{new_count}</b>"
    )

    media_path = os.path.join(IMG_DIR, card["file"])
    if os.path.exists(media_path):
        callback.message.answer_photo(media_path, caption=caption, parse_mode="HTML")
    else:
        callback.message.answer(caption, parse_mode="HTML")

    show_my_crates(callback.message, user_id, edit=True)


def price_text(case):
    return f"{case['price']} 🍬" if case.get("currency") == "candy" else f"{case['price']} 🪙"


def show_case_shop(target, user_id):
    kb = KB()
    text = "📦 <b>Магазин кейсов:</b>\n\n"
    for case_id, case in CASES.items():
        if case_id == "raid":
            continue
        kb.button(text=f"📦 {case['name']} — {price_text(case)}", callback_data=f"case_open_{case_id}_{user_id}")
        text += f"• <b>{case['name']}</b> — {price_text(case)}\n"
    kb.button(text="⬅️ Назад", callback_data=f"menu_back_{user_id}")
    kb.adjust(1)
    if "halloween" in CASES:
        text += "\nВсе карты были взяты с профиля @Mr_blox37, выражаем ему огромную благодарность\n"
    text += "\n📦 Твои кейсы: команда <b>мои кейсы</b>"
    target.answer(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@on_message(text_is("кейс"))
def cmd_case(message):
    show_case_shop(message, message.from_user.id)


@on_callback("case_open_")
def case_open(callback):
    parts = callback.data.split("_")
    case_id = "_".join(parts[2:-1])
    owner_id = int(parts[-1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return

    if case_id == "raid":
        callback.answer("❌ Нельзя", show_alert=True)
        return
    callback.answer()

    case = CASES.get(case_id)
    if not case:
        callback.message.edit_text("❌ Кейс не найден.", reply_markup=back_menu(callback.from_user.id))
        return

    count = get_crate_count(owner_id, case_id)
    text = (
        f"📦 <b>{case['name']}</b>\n\n"
        f"💰 Цена: <b>{price_text(case)}</b>\n"
        f"📦 У тебя: <b>{count}</b> шт.\n\n"
        f"Выбери действие:"
    )
    callback.message.edit_text(text, reply_markup=case_actions_menu(case_id, owner_id), parse_mode="HTML")


@on_callback("case_buy_")
def case_buy(callback):
    parts = callback.data.split("_")
    case_id = "_".join(parts[2:-1])
    owner_id = int(parts[-1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return

    if case_id == "raid":
        callback.answer("❌ Этот кейс нельзя купить", show_alert=True)
        return

    case = CASES.get(case_id)
    if not case:
        callback.answer("Кейс не найден", show_alert=True)
        return

    user_id = callback.from_user.id
    player = get_player(user_id)

    if case.get("currency") == "candy":
        have = player.get("candy") or 0
        if have < case["price"]:
            callback.answer(f"❌ Нужно {case['price']} 🍬 Candy, у тебя {have} 🍬", show_alert=True)
            return
        add_candy(user_id, -case["price"])
    else:
        if player["balance"] < case["price"]:
            callback.answer(f"❌ Нужно {case['price']} 🪙, у тебя {player['balance']} 🪙", show_alert=True)
            return
        add_balance(user_id, -case["price"])
    add_crate(user_id, case_id, 1)

    count = get_crate_count(user_id, case_id)
    callback.answer(f"✅ Куплено! У тебя: {count} шт.", show_alert=True)

    text = (
        f"📦 <b>{case['name']}</b> куплен!\n\n"
        f"Теперь у тебя: <b>{count}</b> шт.\n\n"
        f"Открой командой <b>мои кейсы</b>"
    )
    callback.message.edit_text(text, reply_markup=back_menu(user_id), parse_mode="HTML")


@on_callback("case_cards_")
def case_cards(callback):
    parts = callback.data.split("_")
    case_id = "_".join(parts[2:-1])
    owner_id = int(parts[-1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return
    callback.answer()

    case = CASES.get(case_id)
    if not case:
        callback.answer("Кейс не найден", show_alert=True)
        return

    text = f"🎴 <b>Карты кейса «{case['name']}»</b>\n\n"
    for c in case["cards"]:
        text += f"• <b>{c['name']}</b>\n   Шанс: {c['chance']}% | Сила: {_power_hp(c)} | Продажа: {c['sell']} 🪙\n\n"

    kb = KB()
    kb.button(text="⬅️ Назад", callback_data=f"case_open_{case_id}_{owner_id}")
    kb.adjust(1)
    callback.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="HTML")


@on_message(text_is("база"))
def cmd_base(message):
    user_id = message.from_user.id
    get_player(user_id, message.from_user.username)
    update_username(user_id, message.from_user.username)
    show_base(message, user_id, edit=False)


@on_callback("base_equip_")
def base_equip(callback):
    parts = callback.data.split("_")
    owner_id = int(parts[-1])
    card_name = "_".join(parts[2:-1])

    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return

    old_active = get_active_income_card(owner_id)
    if old_active == card_name:
        callback.answer("Эта карта уже экипирована", show_alert=True)
        return
    if old_active and old_active != card_name:
        set_last_grim_time(owner_id, time.time())

    set_active_income_card(owner_id, card_name)
    callback.answer(f"✅ {card_name} экипирована!", show_alert=True)
    show_base(callback.message, owner_id, edit=True)


@on_callback("base_unequip_")
def base_unequip(callback):
    owner_id = int(callback.data.split("_")[-1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return
    set_active_income_card(owner_id, "")
    set_last_grim_time(owner_id, time.time())
    callback.answer("❌ Карта снята", show_alert=True)
    show_base(callback.message, owner_id, edit=True)


# ======================================================================
# ДОНАТ — оплата через Telegram Stars (currency XTR)
# ======================================================================
DONATE_ITEMS = [
    {"id": "coins1000",   "button": "⭐ 2 — 1000 монет",             "label": "1000 монет",             "stars": 2,  "kind": "coins",        "amount": 1000},
    {"id": "coins5000",   "button": "⭐ 6 — 5000 монет",             "label": "5000 монет",             "stars": 6,  "kind": "coins",        "amount": 5000},
    {"id": "coins10000",  "button": "⭐ 15 — 10000 монет",           "label": "10000 монет",            "stars": 15, "kind": "coins",        "amount": 10000},
    {"id": "privforever", "button": "⭐ 5 — Привилегия навсегда",    "label": "Привилегия навсегда",    "stars": 5,  "kind": "priv_forever"},
    {"id": "priv5h",      "button": "⭐ 1 — Привилегия на 5 часов",  "label": "Привилегия на 5 часов",  "stars": 1,  "kind": "priv_5h"},
    {"id": "support",     "button": "⭐ 1 — Поддержка бота",         "label": "Поддержка бота",         "stars": 1,  "kind": "support"},
]
DONATE_BY_ID = {i["id"]: i for i in DONATE_ITEMS}

DONATE_TEXT = (
    "💎 <b>Поддержка бота</b>\n\n"
    "• 1000 🪙 — 2 ⭐\n"
    "• 5000 🪙 — 6 ⭐\n"
    "• 10000 🪙 — 15 ⭐\n"
    "• 👑 Привилегия навсегда — 5 ⭐\n"
    "• 👑 Привилегия на 5 часов — 1 ⭐\n"
    "• ❤️ Поддержка бота — 1 ⭐"
)


def donate_menu(user_id):
    kb = KB()
    for item in DONATE_ITEMS:
        kb.button(text=item["button"], callback_data=f"donate_buy_{item['id']}_{user_id}")
    kb.button(text="⬅️ Назад", callback_data=f"menu_back_{user_id}")
    kb.adjust(1)
    return kb.as_markup()


def show_donate_shop(target, user_id):
    target.answer(DONATE_TEXT, reply_markup=donate_menu(user_id), parse_mode="HTML")


@on_message(text_is("донат"))
def cmd_donate(message):
    show_donate_shop(message, message.from_user.id)


@on_callback("menu_donate")
def cb_donate(callback):
    callback.answer()
    show_donate_shop(callback.message, callback.from_user.id)


@on_callback("donate_buy_")
def donate_buy(callback):
    parts = callback.data.split("_")
    owner_id = int(parts[-1])
    item_id = "_".join(parts[2:-1])
    if callback.from_user.id != owner_id:
        callback.answer("❌ Это не твоё меню.", show_alert=True)
        return
    item = DONATE_BY_ID.get(item_id)
    if not item:
        callback.answer("❌ Товар не найден", show_alert=True)
        return
    callback.answer()
    try:
        callback.bot.call("sendInvoice", {
            "chat_id": callback.message.chat_id,
            "title": item["label"][:32],
            "description": f"{item['label']} — оплата через Telegram Stars",
            "payload": f"donate:{item['id']}:{owner_id}",
            "currency": "XTR",
            "prices": [{"label": item["label"], "amount": item["stars"]}],
            "provider_token": "",
        })
    except TelegramError as e:
        log("sendInvoice:", e, exc=True)
        callback.message.answer("❌ Не удалось выставить счёт, попробуй позже.")


def apply_donate_payment(user_id, username, item_id):
    item = DONATE_BY_ID.get(item_id)
    if not item:
        return None
    get_player(user_id, username)
    update_username(user_id, username)
    if item["kind"] == "coins":
        add_balance(user_id, item["amount"])
    elif item["kind"] == "priv_forever":
        set_privilege(user_id, "vip", 0)
    elif item["kind"] == "priv_5h":
        set_privilege(user_id, "vip", time.time() + 5 * 3600)
    return item


def handle_successful_payment(m, sp):
    payload = sp.get("invoice_payload", "") or ""
    parts = payload.split(":")
    if len(parts) != 3 or parts[0] != "donate":
        return
    item_id = parts[1]
    username = m.from_user.username or m.from_user.first_name
    item = apply_donate_payment(m.from_user.id, username, item_id)
    if not item:
        return
    if item["kind"] == "support":
        text = "❤️ Спасибо за поддержку бота!"
    elif item["kind"] == "coins":
        text = f"✅ Зачислено <b>{item['amount']}</b> 🪙. Спасибо за донат!"
    else:
        priv_name = PRIVILEGES["vip"]["display"]
        text = f"👑 Привилегия <b>{priv_name}</b> выдана! Спасибо за донат!"
    m.answer(text, parse_mode="HTML")


@on_message(text_is("база собрать"))
def cmd_base_collect(message):
    user_id = message.from_user.id
    get_player(user_id, message.from_user.username)
    update_username(user_id, message.from_user.username)

    active = get_active_income_card(user_id)
    if not active:
        message.answer("❌ Нету карты с доходом.")
        return

    inv = get_inventory(user_id)
    if not any(c["card_name"] == active for c in inv):
        set_active_income_card(user_id, "")
        message.answer("❌ Экипированной карты больше нет в инвентаре. Доход сброшен.")
        return

    card_info = find_card_by_name(active)
    if not card_info:
        message.answer("❌ Карта не найдена.")
        return

    now = time.time()
    last = get_last_grim_time(user_id)
    if last <= 0:
        set_last_grim_time(user_id, now)
        message.answer("⏳ Доход начнёт копиться с этого момента.")
        return

    if active == "Grim Ronin":
        cooldown = 2 * 3600
        elapsed = now - last
        cycles = int(elapsed // cooldown)
        if cycles > 24:
            cycles = 24
        if cycles == 0:
            remaining = int(cooldown - elapsed)
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            message.answer(f"⏳ Доход через <b>{hours}ч {minutes}м</b>", parse_mode="HTML")
            return

        player = get_player(user_id)
        total_change = 0
        log_lines = []
        for i in range(cycles):
            if random.random() < 0.7:
                total_change += 1500
                log_lines.append(f"✅ Цикл {i+1}: +1500 🪙")
            else:
                total_change -= 1999
                log_lines.append(f"❌ Цикл {i+1}: −1999 🪙")

        new_balance = max(0, player["balance"] + total_change)
        update_player(user_id, balance=new_balance)
        set_last_grim_time(user_id, last + cycles * cooldown)

        sign = "+" if total_change >= 0 else "−"
        text = (
            f"🏠 <b>База собрана!</b>\n\nКарта: <b>{active}</b>\nЦиклов: <b>{cycles}</b>\n\n"
            + "\n".join(log_lines) +
            f"\n\n💰 Итого: <b>{sign}{abs(total_change)}</b> 🪙\nБаланс: <b>{new_balance}</b> 🪙"
        )
        message.answer(text, parse_mode="HTML")
        return

    income_per_hour = card_info.get("income", 0)
    cooldown = 3600
    elapsed = now - last
    hours_passed = int(elapsed // cooldown)
    if hours_passed > MAX_INCOME_HOURS:
        hours_passed = MAX_INCOME_HOURS
    if hours_passed == 0:
        remaining = int(cooldown - elapsed)
        minutes = remaining // 60
        secs = remaining % 60
        message.answer(f"⏳ Доход через <b>{minutes}м {secs}с</b>", parse_mode="HTML")
        return

    player = get_player(user_id)
    total_income = income_per_hour * hours_passed
    new_balance = player["balance"] + total_income
    update_player(user_id, balance=new_balance)
    set_last_grim_time(user_id, last + hours_passed * cooldown)

    message.answer(
        f"🏠 <b>База собрана!</b>\n\nКарта: <b>{active}</b>\nЧасов: <b>{hours_passed}</b>\n"
        f"Доход в час: {income_per_hour} 🪙\n\n💰 Итого: <b>+{total_income}</b> 🪙\nБаланс: <b>{new_balance}</b> 🪙",
        parse_mode="HTML"
    )


# ======================================================================
# АДМИНКА
# Команды: вд монеты / вд кенди / вд карту / вд новую карту / забрать карту / обнуление / !база данных
# ======================================================================
def _reply_target(message):
    r = message.reply_to_message
    return r if r is not None and r.from_user is not None else None


@on_message(text_starts("вд монеты"))
def give_coins(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    target_reply = _reply_target(message)

    try:
        if target_reply:
            if len(parts) != 3:
                return message.reply("❌ вд монеты 100 (ответом)")
            amount = int(parts[2])
            target_id = target_reply.from_user.id
            target_name = target_reply.from_user.first_name
            get_player(target_id, target_reply.from_user.username)
        else:
            if len(parts) != 4 or not parts[2].startswith("@"):
                return message.reply("❌ вд монеты @user 100")
            amount = int(parts[3])
            target = find_player_by_username(parts[2][1:])
            if not target:
                return message.reply("❌ Игрок не найден")
            target_id, target_name = target["user_id"], target["username"]
    except ValueError:
        return message.reply("❌ вд монеты @user 100")

    add_balance(target_id, amount)
    message.reply(f"✅ Выдано <b>{amount}</b> 🪙 игроку <b>{target_name}</b>", parse_mode="HTML")


@on_message(text_starts("вд всем монеты"))
def give_coins_all(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 4:
        return message.reply("❌ вд всем монеты 100")
    try:
        amount = int(parts[3])
    except ValueError:
        return message.reply("❌ вд всем монеты 100")

    ids = [r["user_id"] for r in _db().execute("SELECT user_id FROM players").fetchall()]
    for uid in ids:
        add_balance(uid, amount)

    message.reply(f"✅ Выдано <b>{amount}</b> 🪙 всем игрокам ({len(ids)} шт.)", parse_mode="HTML")


@on_message(text_starts("вд кенди"))
def give_candy(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    target_reply = _reply_target(message)

    try:
        if target_reply:
            if len(parts) != 3:
                return message.reply("❌ вд кенди 100 (ответом)")
            amount = int(parts[2])
            target_id = target_reply.from_user.id
            target_name = target_reply.from_user.first_name
            get_player(target_id, target_reply.from_user.username)
        else:
            if len(parts) != 4 or not parts[2].startswith("@"):
                return message.reply("❌ вд кенди @user 100")
            amount = int(parts[3])
            target = find_player_by_username(parts[2][1:])
            if not target:
                return message.reply("❌ Игрок не найден")
            target_id, target_name = target["user_id"], target["username"]
    except ValueError:
        return message.reply("❌ вд кенди @user 100")

    add_candy(target_id, amount)
    message.reply(f"✅ Выдано <b>{amount}</b> 🍬 Candy игроку <b>{target_name}</b>", parse_mode="HTML")


@on_message(text_starts("вд привилегию лидер"))
def give_leader_privilege(message):
    if not is_admin(message.from_user.id):
        return
    target_reply = _reply_target(message)

    if target_reply:
        target_id = target_reply.from_user.id
        target_username = target_reply.from_user.username
        get_player(target_id, target_username)
    else:
        parts = message.text.split()
        if len(parts) != 4 or not parts[3].startswith("@"):
            return message.reply("❌ вд привилегию лидер @user (или ответом на сообщение)")
        target = find_player_by_username(parts[3][1:])
        if not target:
            return message.reply("❌ Игрок не найден")
        target_id, target_username = target["user_id"], target["username"]

    set_privilege(target_id, "leader", 0)
    mention = f"@{target_username}" if target_username else "игрок"
    message.reply(f"🎉 {mention}, поздравляю, тебе дали привилегию за 5 звёзд", parse_mode="HTML")


@on_message(text_starts("вд новую карту"))
def give_new_card_only(message):
    """Выдаёт карту, ТОЛЬКО если её у игрока ещё нет (дубликаты не добавляет)."""
    if not is_admin(message.from_user.id):
        return
    rest = message.text[len("вд новую карту"):].strip()
    target_reply = _reply_target(message)

    if target_reply:
        target_id = target_reply.from_user.id
        target_name = target_reply.from_user.first_name
        get_player(target_id, target_reply.from_user.username)
        card_name = rest.strip('"').strip("'")
    else:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2 or not parts[0].startswith("@"):
            return message.reply('❌ вд новую карту @user "название"')
        target = find_player_by_username(parts[0][1:])
        if not target:
            return message.reply("❌ Игрок не найден")
        target_id, target_name = target["user_id"], target["username"]
        card_name = parts[1].strip('"').strip("'")

    card = find_card_by_name(card_name)
    if not card:
        return message.reply(f"❌ Карта «{card_name}» не найдена")

    if get_card_count(target_id, card["name"]) > 0:
        return message.reply(f"⚠️ У <b>{target_name}</b> уже есть <b>{card['name']}</b> — не трогаю", parse_mode="HTML")

    add_to_inventory(target_id, card["name"], card["rarity"])
    message.reply(f"✅ Выдана новая <b>{card['name']}</b> игроку <b>{target_name}</b>", parse_mode="HTML")


# ВАЖНО: регистрируем "вд карту" после "вд новую карту" — text_starts проверяет префикс,
# а "вд новую карту" не является префиксом "вд карту", так что порядок тут безопасен.
@on_message(text_starts("вд карту"))
def give_card(message):
    if not is_admin(message.from_user.id):
        return
    rest = message.text[len("вд карту"):].strip()
    target_reply = _reply_target(message)

    if target_reply:
        target_id = target_reply.from_user.id
        target_name = target_reply.from_user.first_name
        get_player(target_id, target_reply.from_user.username)
        card_name = rest.strip('"').strip("'")
    else:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2 or not parts[0].startswith("@"):
            return message.reply('❌ вд карту @user "название"')
        target = find_player_by_username(parts[0][1:])
        if not target:
            return message.reply("❌ Игрок не найден")
        target_id, target_name = target["user_id"], target["username"]
        card_name = parts[1].strip('"').strip("'")

    card = find_card_by_name(card_name)
    if not card:
        return message.reply(f"❌ Карта «{card_name}» не найдена")

    add_to_inventory(target_id, card["name"], card["rarity"])
    total = get_card_count(target_id, card["name"])
    message.reply(f"✅ Выдана <b>{card['name']}</b> игроку <b>{target_name}</b>\nУ него: <b>×{total}</b>", parse_mode="HTML")


@on_message(text_starts("забрать карту"))
def take_card(message):
    if not is_admin(message.from_user.id):
        return
    rest = message.text[len("забрать карту"):].strip()
    target_reply = _reply_target(message)

    if target_reply:
        target_id = target_reply.from_user.id
        target_name = target_reply.from_user.first_name
        card_name = rest.strip('"').strip("'")
    else:
        parts = rest.split(maxsplit=1)
        if len(parts) < 2 or not parts[0].startswith("@"):
            return message.reply('❌ забрать карту @user "название"')
        target = find_player_by_username(parts[0][1:])
        if not target:
            return message.reply("❌ Игрок не найден")
        target_id, target_name = target["user_id"], target["username"]
        card_name = parts[1].strip('"').strip("'")

    count = get_card_count(target_id, card_name)
    if count <= 0:
        return message.reply(f"❌ У игрока нет карты «{card_name}»")

    remove_from_inventory(target_id, card_name)
    new_count = get_card_count(target_id, card_name)
    message.reply(f"✅ Забрана <b>{card_name}</b> у <b>{target_name}</b>\nОсталось: <b>×{new_count}</b>", parse_mode="HTML")


@on_message(text_starts("обнуление"))
def reset_user(message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split()
    target_reply = _reply_target(message)

    if target_reply:
        target_id = target_reply.from_user.id
        target_name = target_reply.from_user.first_name
    else:
        if len(parts) != 2 or not parts[1].startswith("@"):
            return message.reply("❌ обнуление @user")
        target = find_player_by_username(parts[1][1:])
        if not target:
            return message.reply("❌ Игрок не найден")
        target_id, target_name = target["user_id"], target["username"]

    reset_player(target_id)
    message.reply(f"✅ Игрок <b>{target_name}</b> обнулён", parse_mode="HTML")


@on_message(text_is("!база данных"))
def send_database_file(message):
    """Молча кидает файл game.db админу — без подписи, без лишнего текста."""
    if not is_admin(message.from_user.id):
        return
    if not os.path.exists(DB_PATH):
        return
    try:
        message.reply_document(DB_PATH)
    except TelegramError as e:
        log("!база данных:", e, exc=True)


@on_message(text_is("!бэкап"))
def force_backup_now(message):
    """Принудительно сохранить game.db в GitHub прямо сейчас (не ждать таймер)."""
    if not is_admin(message.from_user.id):
        return
    if not db_backup.enabled():
        return message.reply("❌ GITHUB_TOKEN/GITHUB_REPO не заданы — бэкап в GitHub выключен")
    ok = db_backup.backup(DB_PATH, log=log)
    message.reply("✅ game.db сохранена в GitHub" if ok else "❌ Бэкап не удался, см. логи")


@on_message(text_is("стата"))
@on_message(text_is("стат"))
def cmd_stats(message):
    if not is_admin(message.from_user.id):
        return
    s = get_stats()
    top = top_players(3)
    medals = ["🥇", "🥈", "🥉"]
    top_lines = []
    for i, r in enumerate(top):
        name = html.escape(r["username"] or "Игрок", quote=False)
        top_lines.append(f"{medals[i]} {name} — {r['balance']} 🪙")
    while len(top_lines) < 3:
        top_lines.append(f"{medals[len(top_lines)]} —")

    text = (
        "📊 Статистика бота\n\n"
        f"👥 Игроков: {s['n']}\n"
        f"💰 Монет: {s['coins']} 🪙\n"
        "🏰 Рейд-монет: Нету рейдов, сорри\n"
        f"🎴 Карт: {s['cards']}\n"
        f"⭐ Уровней: {s['levels']}\n"
        f"🎯 Очков: {s['points']}\n\n"
        "🏆 Топ-3 по монетам:\n" + "\n".join(top_lines)
    )
    message.reply(text, parse_mode="HTML")


@on_message(text_is("админка"))
def admin_help(message):
    if not is_admin(message.from_user.id):
        return
    text = (
        "🛠 <b>Команды админа</b>\n\n"
        "• <code>вд монеты [@user|ответом] 100</code> — выдать монеты\n"
        "• <code>вд всем монеты 100</code> — выдать монеты всем игрокам из бд\n"
        "• <code>вд кенди [@user|ответом] 100</code> — выдать Candy\n"
        "• <code>вд карту [@user|ответом] \"название\"</code> — выдать карту (дубликаты тоже считает)\n"
        "• <code>вд новую карту [@user|ответом] \"название\"</code> — выдать, только если карты ещё нет\n"
        "• <code>вд привилегию лидер [@user|ответом]</code> — выдать Leader Of The Noobs навсегда\n"
        "• <code>забрать карту [@user|ответом] \"название\"</code> — забрать карту\n"
        "• <code>обнуление [@user|ответом]</code> — сбросить прогресс игрока\n"
        "• <code>стата</code> — статистика бота\n"
        "• <code>!база данных</code> — прислать файл game.db\n"
        "• <code>!бэкап</code> — сохранить game.db в GitHub прямо сейчас\n"
        "• <code>админка</code> — этот список"
    )
    message.reply(text, parse_mode="HTML")


# ======================================================================
# КАНАЛ (ответ на пересланные посты в обсуждениях)
# ======================================================================
def _is_channel_post_in_group(m):
    return m.chat_type in ("group", "supergroup") and m.forwarded_from_channel


@on_message(_is_channel_post_in_group)
def reply_to_channel_post(message):
    try:
        message.reply("🎴 Напиши <b>эксперимент</b>, чтобы получить карточку!", parse_mode="HTML")
    except Exception as e:
        log("Ошибка ответа в комментарии:", e)


# ======================================================================
# FALLBACK / ПОМОЩЬ (должен регистрироваться последним — ловит /help)
# ======================================================================
HELP = (
    "🎴 <b>Команды</b>\n\n"
    "• <b>эксперимент</b> — получить карту\n"
    "• <b>призыв</b> — получить карту (простой)\n"
    "• <b>бонус</b> — ежедневные монеты\n"
    "• <b>профиль</b> — баланс и 🍬 Candy\n"
    "• <b>инвентарь</b> / <b>инв</b> — твои карты\n"
    "• <b>база</b> / <b>база собрать</b> — карты дохода\n"
    "• <b>кейс</b> / <b>мои кейсы</b> — кейсы\n"
    "• <b>квесты</b> — квесты\n"
    "• <b>крафты</b> — крафты\n"
    "• <b>донат</b> — поддержка бота ⭐"
)


@on_message(text_is("/help"))
def help_cmd(message):
    text = HELP
    if is_admin(message.from_user.id):
        text += "\n\nТы админ — набери <b>админка</b> для списка админ-команд."
    message.answer(text, parse_mode="HTML")


# ======================================================================
# HEALTH-СЕРВЕР ДЛЯ RENDER
# Render Web Service требует открытый порт $PORT, иначе деплой считается
# неудавшимся. Поллинг Telegram идёт в отдельном потоке, а этот сервер
# просто отвечает "OK" — на него и будет стучаться UptimeRobot.
# ======================================================================
class _HealthHandler(BaseHTTPRequestHandler):
    def _ok(self):
        body = b"OK"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._ok()

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, fmt, *args):
        pass  # не засоряем логи каждым пингом UptimeRobot


def start_health_server():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), _HealthHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    log(f"health-сервер слушает 0.0.0.0:{PORT}")
    return srv


# ======================================================================
# ДИСПЕТЧЕР + ОСНОВНОЙ ЦИКЛ (long polling)
# ======================================================================
SLASH = {"help": "/help", "start": "/start"}

MENU = [
    {"command": "start", "description": "Запуск и меню"},
    {"command": "help", "description": "Список команд"},
]

BOT_USERNAME = ""


def normalize_slash(m):
    if not m.text or not m.text.startswith("/"):
        return True
    cmd = m.text.split(None, 1)[0][1:]
    name, _, target = cmd.partition("@")
    if target and target.lower() != BOT_USERNAME.lower():
        return False
    if name.lower() in SLASH:
        m.text = SLASH[name.lower()]
    return True


def dispatch(bot, upd):
    if "message" in upd:
        raw = upd["message"]
        if "chat" not in raw:
            return
        m = Msg(bot, raw)
        if m.from_user is None and not m.forwarded_from_channel:
            return
        sp = raw.get("successful_payment")
        if sp:
            try:
                handle_successful_payment(m, sp)
            except Exception:
                log("successful_payment:", exc=True)
            return
        if not normalize_slash(m):
            return
        for pred, fn in MESSAGE_HANDLERS:
            if pred(m):
                fn(m)
                return

    elif "callback_query" in upd:
        cb = Callback(bot, upd["callback_query"])
        if cb.message is None:
            cb.answer()
            return
        for prefix, fn in CALLBACK_HANDLERS:
            if cb.data.startswith(prefix):
                fn(cb)
                return
        cb.answer()

    elif "pre_checkout_query" in upd:
        pcq = upd["pre_checkout_query"]
        try:
            bot.call("answerPreCheckoutQuery", {"pre_checkout_query_id": pcq["id"], "ok": True})
        except TelegramError as e:
            log("answerPreCheckoutQuery:", e)


def _handle_shutdown_signal(signum, frame):
    log("получен сигнал остановки (%s) — сохраняю game.db в GitHub перед выходом" % signum)
    try:
        db_backup.backup(DB_PATH, log=log)
    except Exception:
        log("бэкап при остановке не удался", exc=True)
    sys.exit(0)


def main():
    global BOT_USERNAME
    log("старт процесса")

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    start_health_server()

    try:
        db_backup.restore(DB_PATH, log=log)
    except Exception:
        log("восстановление game.db из GitHub не удалось", exc=True)

    if not BOT_TOKEN:
        log("BOT_TOKEN не задан (переменная окружения BOT_TOKEN или setting.env)")
        # Не выходим из процесса: health-сервер держит порт открытым, чтобы
        # Render не считал деплой упавшим, пока токен не пропишут в Dashboard.
        while True:
            time.sleep(60)

    try:
        init_db()
        bot = Bot(BOT_TOKEN)
        me = bot.call("getMe")
        BOT_USERNAME = me.get("username") or ""
        bot.call("deleteWebhook")
    except TelegramError as e:
        log("Telegram не принял токен:", e)
        while True:
            time.sleep(60)
    except Exception:
        log("Старт не удался", exc=True)
        while True:
            time.sleep(60)

    try:
        bot.call("setMyCommands", {"commands": MENU})
    except Exception:
        log("setMyCommands не удалось", exc=True)

    log("старт, @" + BOT_USERNAME)

    db_backup.start_periodic(DB_PATH, log=log)

    offset = None
    while True:
        try:
            updates = bot.call("getUpdates", {
                "offset": offset, "timeout": 30,
                "allowed_updates": ["message", "callback_query", "pre_checkout_query"]}, timeout=45)
        except TelegramError as e:
            log("getUpdates:", e)
            if e.code == 401:
                time.sleep(60)
                continue
            time.sleep(5 if e.code == 409 else 3)
            continue
        except Exception as e:
            log("сеть:", e)
            time.sleep(3)
            continue

        for upd in updates:
            offset = upd["update_id"] + 1
            try:
                dispatch(bot, upd)
            except Exception:
                log("ошибка обработчика", exc=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
