#!/usr/bin/env python3
"""ForumUpdateHelperBot: Telegram assistant for X-Competence forum updates."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import dateparser
from dotenv import load_dotenv
from openai import OpenAI
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).expanduser()
DB_PATH = DATA_DIR / "forum_update_helper.sqlite3"
UPLOADS_DIR = DATA_DIR / "uploads"
UPDATES_DIR = DATA_DIR / "updates"

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
ADMIN_CHAT_ID = int(
    os.getenv("ADMIN_CHAT_ID")
    or os.getenv("ASSISTANTS_TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT_ID")
    or "0"
)
TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
DAILY_MAINTENANCE_TIME = os.getenv("DAILY_MAINTENANCE_TIME", "09:30")
OFFSITE_INTERVAL_DAYS = int(os.getenv("OFFSITE_INTERVAL_DAYS", "90"))
PRE_FORUM_REMINDER_DAYS = tuple(
    int(x.strip()) for x in os.getenv("PRE_FORUM_REMINDER_DAYS", "3").split(",") if x.strip()
)
TELEGRAM_TEXT_LIMIT = int(os.getenv("TELEGRAM_TEXT_LIMIT", "3900"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")
TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")
TRANSCRIBE_LANGUAGE = os.getenv("OPENAI_TRANSCRIBE_LANGUAGE", "ru")
OPENAI_REFLECTION_ENABLED = os.getenv("OPENAI_REFLECTION_ENABLED", "true").casefold() not in {
    "0",
    "false",
    "no",
    "off",
}
BOT_TO_BOT_MESSAGE_PAUSE_SECONDS = float(os.getenv("BOT_TO_BOT_MESSAGE_PAUSE_SECONDS", "2"))

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("forum_update_helper")

_openai = OpenAI() if os.getenv("OPENAI_API_KEY") else None
_message_timing: dict[str, dict[str, Any]] = {}
_message_locks: dict[str, asyncio.Lock] = {}

BUSINESS_CLUBS = ("Атланты", "Эквиум", "К1", "Терра", "Сколково", "Другое")
METHODOLOGY_CLASSIC = "Классическая (YPO)"
METHODOLOGY_STRATEGY = "С личной стратегией (X-Competence)"
METHODOLOGIES = (METHODOLOGY_CLASSIC, METHODOLOGY_STRATEGY)
DEFAULT_METHODOLOGY = METHODOLOGY_CLASSIC
ONBOARDING_TOTAL_STEPS = 7
FORUM_GUIDE_DIR = BASE_DIR / "docs" / "forum-guide"
COMMON_GUIDE_PATH = FORUM_GUIDE_DIR / "forum-common-guide.md"
CLASSIC_GUIDE_PATH = FORUM_GUIDE_DIR / "classic-update.md"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["Подготовить апдейт", "Дата следующего форума"],
        ["Здоровье форум-группы", "Справочник форума"],
        ["Личный кабинет"],
        ["О боте", "Режим дневника"],
        ["Промпт дневника"],
        ["Ищу психолога", "Ищу коуча"],
        ["Сделать собственный бот", "Связаться с автором"],
        ["Удалить мои данные"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

PSYCHOLOGIST_URL = "https://all.achernigova.ru/p/recommendediarpt/"
COACH_URL = "https://5prism.ru/kouchi/"
REPO_URL = "https://github.com/andreyputinktt/forum-update-helper"
BUILD_BOT_DOC_URL = "https://github.com/andreyputinktt/forum-update-helper/blob/main/CREATE_OWN_BOT.md"
AUTHOR_TEXT = (
    "Автор бота: Андрей Путин.\n"
    "Telegram: @utandr\n\n"
    "Можно писать, если хотите связаться с автором, предложить улучшение или "
    "стать соавтором. Бот развёрнут на сервере компании kt.team."
)


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    section: str


SPHERES = ("Моё дело", "Моя семья / близкие", "Я")

UPDATE_QUESTIONS: list[Question] = []
for sphere in SPHERES:
    UPDATE_QUESTIONS.extend(
        [
            Question(
                f"rating_{sphere}",
                f"{sphere}: поставь оценку 1-10. Сравни с прошлым месяцем и коротко опиши, как себя чувствуешь.",
                "Часть 1. Оценка трёх сфер",
            ),
            Question(
                f"changed_{sphere}",
                f"{sphere}: что конкретно изменилось с прошлого раза?",
                "Часть 1. Оценка трёх сфер",
            ),
            Question(
                f"impact_{sphere}",
                f"{sphere}: что дало наибольший вклад в эту оценку?",
                "Часть 1. Оценка трёх сфер",
            ),
            Question(
                f"delta_{sphere}",
                f"{sphere}: если оценка упала — что произошло? Если выросла — за счёт чего?",
                "Часть 1. Оценка трёх сфер",
            ),
        ]
    )

for sphere in SPHERES:
    UPDATE_QUESTIONS.extend(
        [
            Question(
                f"past_plan_{sphere}",
                f"{sphere}: что ты планировал на прошедший период и что получил по факту?",
                "Часть 2. Ретроспектива",
            ),
            Question(
                f"past_action_{sphere}",
                f"{sphere}: какое действие дало максимальный результат?",
                "Часть 2. Ретроспектива",
            ),
            Question(
                f"past_failed_{sphere}",
                f"{sphere}: что не сработало и почему?",
                "Часть 2. Ретроспектива",
            ),
            Question(
                f"next_excellent_{sphere}",
                f"{sphere}: что будет означать для тебя «отлично» через месяц?",
                "Часть 2. Следующий период",
            ),
            Question(
                f"next_control_{sphere}",
                f"{sphere}: что в твоей власти, а что нет?",
                "Часть 2. Следующий период",
            ),
            Question(
                f"next_factors_{sphere}",
                f"{sphere}: какие внешние факторы сейчас поддерживают или мешают?",
                "Часть 2. Следующий период",
            ),
        ]
    )

UPDATE_QUESTIONS.extend(
    [
        Question(
            "main_request_draft",
            "Сформулируй главный запрос в формате «Как мне ... ?» — один вопрос, до 10 слов, в зоне твоего контроля.",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_context",
            "Опиши суть запроса в 2-3 предложениях: почему это важно и на какие сферы влияет?",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_money",
            "Денежный эквивалент проблемы: ущерб/упущенная прибыль в год и сколько ты готов заплатить за лучшее решение?",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_ideal",
            "Идеальный конечный результат: как выглядит идеально разрешённая ситуация?",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_history",
            "Контекст: как и когда начала развиваться ситуация? Что будет, если ничего не менять?",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_tried",
            "Что уже пробовал?",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_options",
            "Назови минимум 3 варианта решения.",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_best",
            "Оптимальный вариант сейчас: если бы не обсуждал с группой, как бы действовал?",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_stop",
            "Стоп-фактор: что останавливает от реализации оптимального решения?",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_help",
            "Какая помощь нужна от группы? Начни фразой: «Поделитесь опытом, как вы...»",
            "Часть 3. Главный запрос",
        ),
    ]
)

CLASSIC_UPDATE_QUESTIONS = [
    Question(
        "classic_business_rating",
        "Бизнес: поставь оценку месяца 1-10.",
        "Классический Update. Оценки месяца",
    ),
    Question(
        "classic_family_rating",
        "Семья: поставь оценку месяца 1-10.",
        "Классический Update. Оценки месяца",
    ),
    Question(
        "classic_personal_rating",
        "Личное: поставь оценку месяца 1-10.",
        "Классический Update. Оценки месяца",
    ),
]

for sphere in ("Бизнес", "Семья", "Личное"):
    for sign, label in (("plus", "плюс"), ("minus", "минус")):
        CLASSIC_UPDATE_QUESTIONS.extend(
            [
                Question(
                    f"classic_{sphere}_{sign}_event",
                    f"{sphere}, {label}: самое важное, что произошло. Одним предложением.",
                    f"Классический Update. {sphere}",
                ),
                Question(
                    f"classic_{sphere}_{sign}_importance",
                    f"{sphere}, {label}: почему эта ситуация важна для тебя?",
                    f"Классический Update. {sphere}",
                ),
                Question(
                    f"classic_{sphere}_{sign}_feelings",
                    f"{sphere}, {label}: какие чувства ты испытываешь? Укажи минимум три чувства.",
                    f"Классический Update. {sphere}",
                ),
            ]
        )

CLASSIC_UPDATE_QUESTIONS.extend(
    [
        Question(
            "classic_presentation_topic",
            "Если бы ты презентовал сегодня, какую текущую ситуацию или возможность в бизнесе, семье или личной жизни хотел бы обсудить с форумом?",
            "Классический Update. Тема для форума",
        ),
        Question(
            "classic_5_percent_joy",
            "Какие 5% самых радостных событий и чувств месяца стоит назвать?",
            "Классический Update. 5%",
        ),
        Question(
            "classic_5_percent_heavy",
            "Какие 5% самых тяжёлых событий и чувств месяца требуют внимания форума?",
            "Классический Update. 5%",
        ),
        Question(
            "classic_main_question",
            "Над чем ты хотел бы поработать? Сформулируй один вопрос или запрос к форуму.",
            "Классический Update. Главный запрос",
        ),
    ]
)

for sphere in SPHERES:
    UPDATE_QUESTIONS.append(
        Question(
            f"annual_goal_{sphere}",
            f"{sphere}: какая годовая цель и как сегодняшний запрос с ней связан?",
            "Часть 4. Связь с годовыми целями",
        )
    )

UPDATE_QUESTIONS.extend(
    [
        Question(
            "meeting_insights",
            "На встрече: какие ключевые инсайты и мысли хочешь фиксировать, пока слушаешь других?",
            "Часть 5. Личный план действий",
        ),
        Question(
            "meeting_questions",
            "На какие вопросы теперь будешь искать ответ?",
            "Часть 5. Личный план действий",
        ),
        Question(
            "meeting_others",
            "Что хочешь вынести из запросов других участников?",
            "Часть 5. Личный план действий",
        ),
        Question(
            "meeting_gratitude",
            "Благодарность себе и другим: что важно не забыть проговорить?",
            "Часть 5. Личный план действий",
        ),
        Question(
            "next_actions",
            "Действия в ближайшее время: глагол совершенного вида, где/когда/с помощью чего/какой результат, плюс срок.",
            "Часть 5. Личный план действий",
        ),
    ]
)

HEALTH_QUESTIONS = [
    Question("energy", "Оцени энергию форум-группы после встречи по шкале 1-10. Почему так?", "Здоровье группы"),
    Question("trust", "Оцени доверие и безопасность в группе по шкале 1-10. Что повлияло?", "Здоровье группы"),
    Question("depth", "Насколько глубокой была работа с запросами? Где ушли в поверхность?", "Здоровье группы"),
    Question("rules", "Соблюдалось ли правило форума: без советов, только личный опыт от первого лица?", "Здоровье группы"),
    Question("participation", "Кто включался сильнее всего, а кто выпадал или молчал?", "Здоровье группы"),
    Question("tension", "Были ли напряжения, скрытые конфликты, невыраженные темы?", "Здоровье группы"),
    Question("value", "Что было самым ценным для группы на этой встрече?", "Здоровье группы"),
    Question("improve", "Что стоит усилить к следующему форуму?", "Здоровье группы"),
]


def now_iso() -> str:
    return datetime.now(TZ).isoformat(timespec="seconds")


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=False)


def clip(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def mark_user_activity(chat_id: int | str) -> None:
    state = _message_timing.setdefault(str(chat_id), {})
    state["last_actor"] = "user"


async def spaced_bot_send(chat_id: int | str, send_call: Any) -> Any:
    key = str(chat_id)
    lock = _message_locks.setdefault(key, asyncio.Lock())
    async with lock:
        state = _message_timing.setdefault(key, {})
        if state.get("last_actor") == "bot":
            loop = asyncio.get_running_loop()
            elapsed = loop.time() - float(state.get("last_bot_at") or 0)
            pause = BOT_TO_BOT_MESSAGE_PAUSE_SECONDS - elapsed
            if pause > 0:
                await asyncio.sleep(pause)
        result = await send_call()
        state["last_actor"] = "bot"
        state["last_bot_at"] = asyncio.get_running_loop().time()
        return result


def parse_time(value: str) -> time:
    hour, minute = value.split(":", 1)
    return time(int(hour), int(minute), tzinfo=TZ)


def parse_forum_date(value: str, base: date | None = None) -> date | None:
    value = value.strip()
    if not value:
        return None
    base_date = base or datetime.now(TZ).date()
    short_match = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.?", value)
    if short_match:
        day = int(short_match.group(1))
        month = int(short_match.group(2))
        try:
            parsed = date(base_date.year, month, day)
        except ValueError:
            return None
        if parsed < base_date:
            parsed = date(base_date.year + 1, month, day)
        return parsed
    settings = {
        "PREFER_DATES_FROM": "future",
        "RELATIVE_BASE": datetime.combine(base_date, time(12, 0), tzinfo=TZ),
        "RETURN_AS_TIMEZONE_AWARE": False,
    }
    parsed = dateparser.parse(value, languages=["ru", "en"], settings=settings)
    if parsed is None:
        return None
    return parsed.date()


def default_full_name(user: dict[str, Any]) -> str:
    return f"Участник форума {user.get('telegram_user_id') or 'без имени'}"


def default_forum_group(user: dict[str, Any]) -> str:
    return f"Форум-группа {user.get('telegram_user_id') or 'без названия'}"


def default_forum_date() -> date:
    return datetime.now(TZ).date() + timedelta(days=30)


def normalize_username(value: str) -> str:
    return value.strip().removeprefix("@").casefold()


def normalize_report_recipient(value: str) -> str:
    username = normalize_username(value)
    if not re.fullmatch(r"[a-z0-9_]{5,32}", username):
        return ""
    return f"@{username}"


def is_profile_complete(user: dict[str, Any]) -> bool:
    return all(
        user.get(field)
        for field in ("business_club", "full_name", "forum_group", "next_forum_date")
    ) and user.get("keep_files") is not None


def normalize_methodology(value: Any) -> str | None:
    methodology = str(value or "").strip()
    if not methodology:
        return None
    if methodology in METHODOLOGIES:
        return methodology
    folded = methodology.casefold()
    if folded in {"ypo", "классическая", "classic", "классика"}:
        return METHODOLOGY_CLASSIC
    if "x-competence" in folded or "x competence" in folded or "личной стратег" in folded:
        return METHODOLOGY_STRATEGY
    return None


def methodology_for_user(user: dict[str, Any]) -> str:
    return normalize_methodology(user.get("methodology")) or DEFAULT_METHODOLOGY


def update_questions_for_user(user: dict[str, Any]) -> list[Question]:
    if methodology_for_user(user) == METHODOLOGY_CLASSIC:
        return CLASSIC_UPDATE_QUESTIONS
    return UPDATE_QUESTIONS


def load_forum_guide_context(methodology: str | None = None, max_chars: int = 18000) -> str:
    parts: list[str] = []
    normalized = normalize_methodology(methodology)
    for path in (COMMON_GUIDE_PATH, CLASSIC_GUIDE_PATH):
        if path == CLASSIC_GUIDE_PATH and normalized and normalized != METHODOLOGY_CLASSIC:
            continue
        if path.exists():
            parts.append(path.read_text(encoding="utf-8").strip())
    context = "\n\n---\n\n".join(part for part in parts if part)
    if len(context) <= max_chars:
        return context
    return context[:max_chars].rstrip() + "\n\n[контекст обрезан]"


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_user_id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                business_club TEXT,
                full_name TEXT,
                forum_group TEXT,
                methodology TEXT DEFAULT 'Классическая (YPO)',
                community_chat TEXT,
                keep_files INTEGER,
                state TEXT,
                active_flow TEXT,
                active_step INTEGER DEFAULT 0,
                flow_payload TEXT DEFAULT '{}',
                next_forum_date TEXT,
                diary_enabled INTEGER DEFAULT 0,
                diary_feedback_prompt TEXT,
                last_offsite_reminder_date TEXT,
                admin_notified INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_user_id INTEGER,
                kind TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS reminder_log (
                telegram_user_id INTEGER NOT NULL,
                reminder_type TEXT NOT NULL,
                reminder_key TEXT NOT NULL,
                sent_at TEXT NOT NULL,
                PRIMARY KEY (telegram_user_id, reminder_type, reminder_key)
            );
            """
        )
        self._ensure_column("users", "methodology", "TEXT DEFAULT 'Классическая (YPO)'")
        self._ensure_column("users", "diary_enabled", "INTEGER DEFAULT 0")
        self._ensure_column("users", "diary_feedback_prompt", "TEXT")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {row["name"] for row in rows}:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def ensure_user(self, update: Update) -> dict[str, Any]:
        tg_user = update.effective_user
        chat = update.effective_chat
        assert tg_user is not None and chat is not None
        mark_user_activity(chat.id)
        existing = self.get_user(tg_user.id)
        timestamp = now_iso()
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO users (
                    telegram_user_id, chat_id, username, first_name, last_name,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tg_user.id,
                    chat.id,
                    tg_user.username,
                    tg_user.first_name,
                    tg_user.last_name,
                    timestamp,
                    timestamp,
                ),
            )
        else:
            self.conn.execute(
                """
                UPDATE users
                SET chat_id = ?, username = ?, first_name = ?, last_name = ?, updated_at = ?
                WHERE telegram_user_id = ?
                """,
                (chat.id, tg_user.username, tg_user.first_name, tg_user.last_name, timestamp, tg_user.id),
            )
        self.conn.commit()
        return self.get_user(tg_user.id) or {}

    def get_user(self, telegram_user_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM users WHERE telegram_user_id = ?",
            (telegram_user_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        key = normalize_username(username)
        if not key:
            return None
        row = self.conn.execute(
            "SELECT * FROM users WHERE lower(username) = ?",
            (key,),
        ).fetchone()
        return dict(row) if row else None

    def complete_users(self) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM users WHERE full_name IS NOT NULL").fetchall()
        return [dict(row) for row in rows]

    def update_user(self, telegram_user_id: int, **fields: Any) -> dict[str, Any]:
        fields["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [telegram_user_id]
        self.conn.execute(f"UPDATE users SET {assignments} WHERE telegram_user_id = ?", values)
        self.conn.commit()
        return self.get_user(telegram_user_id) or {}

    def set_flow(
        self,
        telegram_user_id: int,
        flow: str | None,
        step: int = 0,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.update_user(
            telegram_user_id,
            active_flow=flow,
            active_step=step,
            flow_payload=json.dumps(payload or {}, ensure_ascii=False),
            state=None,
        )

    def payload(self, user: dict[str, Any]) -> dict[str, Any]:
        try:
            return json.loads(user.get("flow_payload") or "{}")
        except json.JSONDecodeError:
            return {}

    def log_interaction(self, telegram_user_id: int | None, kind: str) -> None:
        self.conn.execute(
            "INSERT INTO interactions (telegram_user_id, kind, created_at) VALUES (?, ?, ?)",
            (telegram_user_id, kind, now_iso()),
        )
        self.conn.commit()

    def reminder_sent(self, telegram_user_id: int, reminder_type: str, reminder_key: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM reminder_log
            WHERE telegram_user_id = ? AND reminder_type = ? AND reminder_key = ?
            """,
            (telegram_user_id, reminder_type, reminder_key),
        ).fetchone()
        return row is not None

    def mark_reminder(self, telegram_user_id: int, reminder_type: str, reminder_key: str) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO reminder_log
            (telegram_user_id, reminder_type, reminder_key, sent_at)
            VALUES (?, ?, ?, ?)
            """,
            (telegram_user_id, reminder_type, reminder_key, now_iso()),
        )
        self.conn.commit()

    def stats(self) -> dict[str, int]:
        users = self.conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
        interactions = self.conn.execute("SELECT COUNT(*) AS n FROM interactions").fetchone()["n"]
        complete = self.conn.execute(
            "SELECT COUNT(*) AS n FROM users WHERE full_name IS NOT NULL AND business_club IS NOT NULL"
        ).fetchone()["n"]
        return {"users": users, "complete_users": complete, "interactions": interactions}

    def delete_user(self, telegram_user_id: int) -> None:
        self.conn.execute("DELETE FROM reminder_log WHERE telegram_user_id = ?", (telegram_user_id,))
        self.conn.execute("DELETE FROM interactions WHERE telegram_user_id = ?", (telegram_user_id,))
        self.conn.execute("DELETE FROM users WHERE telegram_user_id = ?", (telegram_user_id,))
        self.conn.commit()
        for directory in (UPLOADS_DIR / str(telegram_user_id), UPDATES_DIR / str(telegram_user_id)):
            if directory.exists():
                shutil.rmtree(directory)


store = Store(DB_PATH)


def main_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Подготовить апдейт", callback_data="menu:update"),
                InlineKeyboardButton("Дата форума", callback_data="menu:date"),
            ],
            [
                InlineKeyboardButton("Health check", callback_data="menu:health"),
                InlineKeyboardButton("Справочник", callback_data="guide:open"),
            ],
            [InlineKeyboardButton("Личный кабинет", callback_data="profile:show")],
            [
                InlineKeyboardButton("Режим дневника", callback_data="diary:mode"),
                InlineKeyboardButton("Промпт дневника", callback_data="diary:prompt"),
            ],
            [InlineKeyboardButton("О боте", callback_data="menu:about")],
            [
                InlineKeyboardButton("Ищу психолога", url=PSYCHOLOGIST_URL),
                InlineKeyboardButton("Ищу коуча", url=COACH_URL),
            ],
            [InlineKeyboardButton("Сделать собственный бот", url=BUILD_BOT_DOC_URL)],
            [InlineKeyboardButton("Связаться с автором", callback_data="menu:author")],
            [InlineKeyboardButton("Удалить мои данные", callback_data="delete:ask")],
        ]
    )


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Отменить сценарий", callback_data="flow:cancel")]])


def skip_keyboard(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Пропустить", callback_data=f"skip:{field}")]])


def business_club_keyboard(prefix: str = "club") -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(club, callback_data=f"{prefix}:{club}")] for club in BUSINESS_CLUBS]
    if prefix == "club":
        buttons.append([InlineKeyboardButton("Пропустить", callback_data="skip:business_club")])
    return InlineKeyboardMarkup(buttons)


def methodology_keyboard(prefix: str = "methodology") -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(value, callback_data=f"{prefix}:{value}")] for value in METHODOLOGIES]
    return InlineKeyboardMarkup(buttons)


def guide_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Задать вопрос по справочнику", callback_data="guide:ask")],
            [InlineKeyboardButton("Отмена", callback_data="flow:cancel")],
        ]
    )


def flow_keyboard(show_next: bool = False) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if show_next:
        buttons.append([InlineKeyboardButton("Далее", callback_data="flow:next")])
    buttons.append([InlineKeyboardButton("Отменить сценарий", callback_data="flow:cancel")])
    return InlineKeyboardMarkup(buttons)


async def safe_send(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int | str,
    text: str,
    **kwargs: Any,
) -> bool:
    try:
        parse_mode = kwargs.pop("parse_mode", ParseMode.HTML)
        await spaced_bot_send(
            chat_id,
            lambda: context.bot.send_message(
                chat_id=chat_id,
                text=clip(text),
                parse_mode=parse_mode,
                **kwargs,
            ),
        )
        return True
    except TelegramError as exc:
        log.warning("send_message failed chat_id=%s error=%s", chat_id, exc)
        return False


async def reply(update: Update, text: str, **kwargs: Any) -> None:
    if update.effective_message is None:
        return
    chat_id = update.effective_chat.id if update.effective_chat else update.effective_message.chat_id
    parse_mode = kwargs.pop("parse_mode", ParseMode.HTML)
    await spaced_bot_send(
        chat_id,
        lambda: update.effective_message.reply_text(
            clip(text),
            parse_mode=parse_mode,
            **kwargs,
        ),
    )


async def cmd_getid(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    store.log_interaction(update.effective_user.id if update.effective_user else None, "getid")
    await reply(
        update,
        f"<b>Chat ID</b>: <code>{esc(update.effective_chat.id)}</code>\n\n"
        "Для админ-уведомлений добавь в .env:\n"
        f"<code>ADMIN_CHAT_ID={esc(update.effective_chat.id)}</code>",
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "start")
    if is_profile_complete(user):
        await show_menu(update)
        return
    await start_onboarding(update, context, user)


async def start_onboarding(update: Update, _context: ContextTypes.DEFAULT_TYPE, user: dict[str, Any]) -> None:
    text = (
        "<b>ForumUpdateHelperBot</b>\n\n"
        "Я помогу подготовиться к форуму в методике «Классическая (YPO)» "
        "или «С личной стратегией (X-Competence)»: проведу по вопросам "
        "апдейта, напомню о дате форума, после встречи соберу здоровье "
        "группы и раз в три месяца предложу личную стратегическую сессию "
        "вне города.\n\n"
        "Ещё я могу помочь вести дневник, чтобы апдейт получился глубже, "
        "а жизнь — более осознанной, насыщенной и особенной.\n\n"
        "Можно отвечать текстом или голосом. Голос я транскрибирую и покажу текст."
    )
    await reply(update, text)
    store.update_user(user["telegram_user_id"], state="onboarding:business_club")
    await reply(
        update,
        f"<b>Шаг 1/{ONBOARDING_TOTAL_STEPS}</b>\nВыбери бизнес-клуб.",
        reply_markup=business_club_keyboard(),
    )


async def show_menu(update: Update) -> None:
    await reply(
        update,
        "<b>Меню</b>\nВыбери действие. Частые кнопки также закреплены внизу чата.",
        reply_markup=MAIN_KEYBOARD,
    )
    await reply(update, "Быстрые действия:", reply_markup=main_inline_keyboard())


async def cmd_menu(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "menu")
    if not is_profile_complete(user):
        await start_onboarding(update, _context, user)
        return
    await show_menu(update)


async def cmd_about(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "about")
    await send_about(update)


async def send_about(update: Update) -> None:
    await reply(
        update,
        "<b>О боте</b>\n\n"
        "Я готовлю форумный апдейт в форматах «Классическая (YPO)» и "
        "«С личной стратегией (X-Competence)»: "
        "провожу по вопросам, собираю файл и помогаю свериться со справочником форума.\n\n"
        "Что умею:\n"
        "• спрашивать дату следующего форума при старте;\n"
        "• за 3 дня до форума начинать подготовку с первого вопроса апдейта;\n"
        "• спрашивать дату следующего форума и использовать её для напоминаний;\n"
        "• проводить весь апдейт вопрос за вопросом с кнопкой «Далее» и счётчиком;\n"
        "• отвечать на вопросы по сохранённым материалам форума;\n"
        "• принимать голосовые ответы и показывать транскрипт;\n"
        "• работать в режиме дневника и давать обратную связь по твоему prompt;\n"
        "• на следующее утро после форума спрашивать здоровье группы;\n"
        "• раз в три месяца напоминать о личной стратегической сессии в отеле;\n"
        "• удалить твои данные с сервера по кнопке.\n\n"
        f'<a href="{REPO_URL}">Репозиторий</a> · '
        f'<a href="{BUILD_BOT_DOC_URL}">Сделать собственный бот</a>',
        reply_markup=main_inline_keyboard(),
    )


async def cmd_cancel(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.set_flow(user["telegram_user_id"], None)
    store.update_user(user["telegram_user_id"], state=None)
    store.log_interaction(user["telegram_user_id"], "cancel")
    await reply(update, "Сценарий остановлен. Возвращаю меню.", reply_markup=MAIN_KEYBOARD)


async def cmd_next_forum(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "next_forum")
    await ask_next_forum_date(update, user)


async def ask_next_forum_date(update: Update, user: dict[str, Any]) -> None:
    store.update_user(user["telegram_user_id"], state="awaiting_next_forum_date", active_flow=None)
    await reply(
        update,
        "<b>Когда следующий форум?</b>\n\n"
        "Напиши дату: например, <code>23.06.2026</code>, <code>2026-06-23</code> или голосом.",
        reply_markup=cancel_keyboard(),
    )


async def cmd_prepare(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "prepare_update")
    await start_update_flow(update, user)


async def cmd_health(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "health")
    await start_health_flow(update, user)


async def cmd_guide(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "guide")
    await show_forum_guide(update, user)


async def cmd_profile(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "profile")
    await show_profile_cabinet(update, user)


async def cmd_diary(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "diary")
    await start_diary_prompt_setup(update, user, enable=True)


async def cmd_stats(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    if ADMIN_CHAT_ID and update.effective_chat.id != ADMIN_CHAT_ID:
        await reply(update, "Статистика доступна только администратору.")
        return
    stats = store.stats()
    await reply(
        update,
        "<b>Статистика</b>\n"
        f"Пользователей всего: <b>{stats['users']}</b>\n"
        f"Заполнили профиль: <b>{stats['complete_users']}</b>\n"
        f"Обращений/действий: <b>{stats['interactions']}</b>",
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    user = store.ensure_user(update)
    data = query.data or ""
    store.log_interaction(user["telegram_user_id"], f"callback:{data.split(':', 1)[0]}")

    if data.startswith("club:"):
        await handle_onboarding_text(update, context, data.split(":", 1)[1])
    elif data.startswith("methodology:"):
        if user.get("state") != "onboarding:methodology":
            await reply(update, "Эта кнопка уже неактуальна. Методика меняется в личном кабинете.")
            return
        await handle_onboarding_text(update, context, data.split(":", 1)[1])
    elif data.startswith("keep:"):
        await handle_onboarding_text(update, context, "да" if data.endswith("1") else "нет")
    elif data.startswith("skip:"):
        await handle_onboarding_skip(update, context, data.split(":", 1)[1])
    elif data == "menu:update":
        await start_update_flow(update, user)
    elif data == "menu:date":
        await ask_next_forum_date(update, user)
    elif data == "menu:health":
        await start_health_flow(update, user)
    elif data == "guide:open":
        await show_forum_guide(update, user)
    elif data == "guide:ask":
        await start_guide_question(update, user)
    elif data == "profile:show":
        await show_profile_cabinet(update, user)
    elif data.startswith("profile:edit:"):
        await start_profile_edit(update, user, data.rsplit(":", 1)[1])
    elif data.startswith("profile:club:"):
        updated = store.update_user(user["telegram_user_id"], business_club=data.split(":", 2)[2], state=None)
        await reply(update, "Бизнес-клуб обновлён.")
        await show_profile_cabinet(update, updated)
    elif data.startswith("profile:methodology:"):
        updated = store.update_user(user["telegram_user_id"], methodology=data.split(":", 2)[2], state=None)
        await reply(update, "Методика обновлена.")
        await show_profile_cabinet(update, updated)
    elif data.startswith("profile:keep:"):
        updated = store.update_user(
            user["telegram_user_id"],
            keep_files=1 if data.endswith("1") else 0,
            state=None,
        )
        await reply(update, "Настройка хранения файлов обновлена.")
        await show_profile_cabinet(update, updated)
    elif data == "profile:clear_community":
        updated = store.update_user(user["telegram_user_id"], community_chat="", state=None)
        await reply(update, "Получатель отчётов очищен. Отчёты останутся в личном чате.")
        await show_profile_cabinet(update, updated)
    elif data == "menu:about":
        await send_about(update)
    elif data == "menu:author":
        await reply(update, esc(AUTHOR_TEXT))
    elif data == "diary:mode":
        await show_diary_mode_menu(update, user)
    elif data == "diary:prompt":
        await start_diary_prompt_setup(update, user, enable=True)
    elif data == "diary:enable":
        await start_diary_prompt_setup(update, user, enable=True)
    elif data == "diary:disable":
        store.update_user(user["telegram_user_id"], diary_enabled=0, state=None)
        await reply(update, "Режим дневника выключен.", reply_markup=MAIN_KEYBOARD)
    elif data == "delete:ask":
        await ask_delete_data(update)
    elif data == "delete:confirm":
        await delete_my_data(update, user)
    elif data == "delete:cancel":
        await reply(update, "Ок, данные оставил.", reply_markup=MAIN_KEYBOARD)
    elif data == "flow:cancel":
        store.set_flow(user["telegram_user_id"], None)
        store.update_user(user["telegram_user_id"], state=None)
        await reply(update, "Сценарий остановлен.", reply_markup=MAIN_KEYBOARD)
    elif data == "flow:next":
        await handle_flow_next(update, context, user)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    text = update.effective_message.text if update.effective_message else ""
    store.log_interaction(user["telegram_user_id"], "text")
    await route_text(update, context, user, text.strip())


async def route_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    text: str,
) -> None:
    if not text:
        await reply(update, "Пустой ответ не записал. Напиши текстом или голосом.")
        return

    lower = text.casefold()
    shortcuts = {
        "подготовить апдейт": lambda: start_update_flow(update, user),
        "дата следующего форума": lambda: ask_next_forum_date(update, user),
        "здоровье форум-группы": lambda: start_health_flow(update, user),
        "справочник форума": lambda: show_forum_guide(update, user),
        "личный кабинет": lambda: show_profile_cabinet(update, user),
        "о боте": lambda: send_about(update),
        "режим дневника": lambda: show_diary_mode_menu(update, user),
        "промпт дневника": lambda: start_diary_prompt_setup(update, user, enable=True),
        "сделать собственный бот": lambda: reply(
            update,
            f'<a href="{BUILD_BOT_DOC_URL}">Инструкция: сделать собственный бот</a>\n'
            f'<a href="{REPO_URL}">Репозиторий</a>',
        ),
        "ищу психолога": lambda: reply(update, f'<a href="{PSYCHOLOGIST_URL}">Рекомендованные психологи</a>'),
        "ищу коуча": lambda: reply(update, f'<a href="{COACH_URL}">Коучи 5 Prism</a>'),
        "связаться с автором": lambda: reply(update, esc(AUTHOR_TEXT)),
        "удалить мои данные": lambda: ask_delete_data(update),
    }
    if lower in shortcuts and not user.get("state") and not user.get("active_flow"):
        await shortcuts[lower]()
        return

    if not is_profile_complete(user):
        await handle_onboarding_text(update, context, text)
        return

    if user.get("state") == "awaiting_next_forum_date":
        await handle_next_forum_date(update, context, user, text)
        return

    if user.get("state") == "diary:prompt":
        await save_diary_prompt(update, user, text)
        return

    if user.get("state") == "guide:question":
        await answer_guide_question(update, context, user, text)
        return

    if str(user.get("state") or "").startswith("profile:"):
        await handle_profile_edit_text(update, user, text)
        return

    if user.get("state") == "flow:await_next" and user.get("active_flow"):
        await reply(update, "Ответ записан. Нажми «Далее», чтобы перейти к следующему вопросу.", reply_markup=flow_keyboard(True))
        return

    if user.get("active_flow") == "update":
        await handle_question_answer(update, context, user, text, update_questions_for_user(user))
        return

    if user.get("active_flow") == "health":
        await handle_question_answer(update, context, user, text, HEALTH_QUESTIONS)
        return

    if user.get("diary_enabled"):
        await handle_diary_entry(update, context, user, text)
        return

    await reply(
        update,
        "Принял. Сейчас активного сценария нет — выбери действие в меню.",
        reply_markup=MAIN_KEYBOARD,
    )


async def handle_onboarding_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
) -> None:
    user = store.ensure_user(update)
    state = user.get("state") or "onboarding:business_club"

    if state == "onboarding:business_club":
        club = text.strip()
        if club not in BUSINESS_CLUBS:
            club = club[:80]
        store.update_user(user["telegram_user_id"], business_club=club, state="onboarding:full_name")
        await reply(
            update,
            f"<b>Шаг 2/{ONBOARDING_TOTAL_STEPS}</b>\nНапиши Фамилию Имя.",
            reply_markup=skip_keyboard("full_name"),
        )
        return

    if state == "onboarding:full_name":
        store.update_user(user["telegram_user_id"], full_name=text[:160], state="onboarding:forum_group")
        await reply(
            update,
            f"<b>Шаг 3/{ONBOARDING_TOTAL_STEPS}</b>\nКак называется твоя форум-группа?",
            reply_markup=skip_keyboard("forum_group"),
        )
        return

    if state == "onboarding:forum_group":
        store.update_user(user["telegram_user_id"], forum_group=text[:160], state="onboarding:methodology")
        await reply(
            update,
            f"<b>Шаг 4/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Выбери методику подготовки апдейта.",
            reply_markup=methodology_keyboard(),
        )
        return

    if state == "onboarding:methodology":
        methodology = normalize_methodology(text)
        if methodology is None:
            await reply(
                update,
                "Методику нужно выбрать, этот шаг нельзя пропустить.\n\n"
                "Выбери один из двух вариантов:",
                reply_markup=methodology_keyboard(),
            )
            return
        store.update_user(user["telegram_user_id"], methodology=methodology, state="onboarding:community_chat")
        await reply(
            update,
            f"<b>Шаг 5/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Кому отправлять статистику о здоровье форум-группы?\n\n"
            "Пришли Telegram username пользователя, например <code>@utandr</code>. "
            "Бот сможет отправить ему отчёт, только если этот пользователь уже запускал бота.",
            reply_markup=skip_keyboard("community_chat"),
        )
        return

    if state == "onboarding:community_chat":
        recipient = normalize_report_recipient(text)
        if not recipient:
            await reply(
                update,
                "Пришли Telegram username в формате <code>@username</code> или нажми «Пропустить».",
                reply_markup=skip_keyboard("community_chat"),
            )
            return
        store.update_user(user["telegram_user_id"], community_chat=recipient, state="onboarding:keep_files")
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Сохранять", callback_data="keep:1"),
                    InlineKeyboardButton("Удалять", callback_data="keep:0"),
                ],
                [InlineKeyboardButton("Пропустить", callback_data="skip:keep_files")],
            ]
        )
        await reply(
            update,
            f"<b>Шаг 6/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Сохранять файлы апдейтов и загруженные документы на сервере или удалять после обработки?\n\n"
            "Голосовые и audio я не сохраняю никогда — удаляю сразу после транскрибации.",
            reply_markup=keyboard,
        )
        return

    if state == "onboarding:keep_files":
        keep_files = text.strip().casefold() in {"да", "сохранять", "save", "yes", "y", "1"}
        user = store.update_user(
            user["telegram_user_id"],
            keep_files=1 if keep_files else 0,
            state="onboarding:next_forum_date",
        )
        await reply(
            update,
            f"<b>Шаг 7/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Когда следующий форум? Напиши дату: например, <code>23.06.2026</code> "
            "или <code>2026-06-23</code>.",
            reply_markup=skip_keyboard("next_forum_date"),
        )
        return

    if state == "onboarding:next_forum_date":
        forum_date = parse_forum_date(text)
        if forum_date is None:
            await reply(update, "Не распознал дату. Попробуй так: <code>23.06.2026</code>.")
            return
        user = store.update_user(
            user["telegram_user_id"],
            next_forum_date=forum_date.isoformat(),
            state=None,
        )
        await reply(
            update,
            f"Профиль готов. Следующий форум: <b>{forum_date.strftime('%d.%m.%Y')}</b>.",
            reply_markup=MAIN_KEYBOARD,
        )
        await notify_admin_new_user(context, user)
        await send_about(update)


async def handle_onboarding_skip(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    field: str,
) -> None:
    user = store.ensure_user(update)
    state = user.get("state") or ""
    expected = state.removeprefix("onboarding:")
    if field != expected:
        await reply(update, "Эта кнопка уже неактуальна. Продолжаем текущий шаг.")
        return

    if field == "methodology":
        await reply(
            update,
            "Методику нужно выбрать, этот шаг нельзя пропустить.",
            reply_markup=methodology_keyboard(),
        )
        return

    if field == "business_club":
        user = store.update_user(user["telegram_user_id"], business_club="Другое", state="onboarding:full_name")
        await reply(
            update,
            f"Ок, поставил бизнес-клуб <b>Другое</b>.\n\n"
            f"<b>Шаг 2/{ONBOARDING_TOTAL_STEPS}</b>\nНапиши Фамилию Имя.",
            reply_markup=skip_keyboard("full_name"),
        )
        return

    if field == "full_name":
        name = default_full_name(user)
        group = default_forum_group(user)
        user = store.update_user(
            user["telegram_user_id"],
            business_club="Другое",
            full_name=name,
            forum_group=group,
            state="onboarding:methodology",
        )
        await reply(
            update,
            f"Ок, заполнил имя как <b>{esc(name)}</b>, форум-группу как "
            f"<b>{esc(group)}</b>, бизнес-клуб как <b>Другое</b>.\n\n"
            f"<b>Шаг 4/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Выбери методику подготовки апдейта.",
            reply_markup=methodology_keyboard(),
        )
        return

    if field == "forum_group":
        value = default_forum_group(user)
        user = store.update_user(user["telegram_user_id"], forum_group=value, state="onboarding:methodology")
        await reply(
            update,
            f"Ок, заполнил форум-группу как <b>{esc(value)}</b>.\n\n"
            f"<b>Шаг 4/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Выбери методику подготовки апдейта.",
            reply_markup=methodology_keyboard(),
        )
        return

    if field == "community_chat":
        user = store.update_user(user["telegram_user_id"], community_chat="", state="onboarding:keep_files")
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Сохранять", callback_data="keep:1"),
                    InlineKeyboardButton("Удалять", callback_data="keep:0"),
                ],
                [InlineKeyboardButton("Пропустить", callback_data="skip:keep_files")],
            ]
        )
        await reply(
            update,
            "Ок, отчёты о здоровье пока будут оставаться в личном чате.\n\n"
            f"<b>Шаг 6/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Сохранять файлы апдейтов и загруженные документы на сервере или удалять после обработки?\n\n"
            "Голосовые и audio я не сохраняю никогда — удаляю сразу после транскрибации.",
            reply_markup=keyboard,
        )
        return

    if field == "keep_files":
        user = store.update_user(user["telegram_user_id"], keep_files=0, state="onboarding:next_forum_date")
        await reply(
            update,
            "Ок, по умолчанию буду удалять файлы после обработки.\n\n"
            f"<b>Шаг 7/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Когда следующий форум?",
            reply_markup=skip_keyboard("next_forum_date"),
        )
        return

    if field == "next_forum_date":
        forum_date = default_forum_date()
        user = store.update_user(
            user["telegram_user_id"],
            next_forum_date=forum_date.isoformat(),
            state=None,
        )
        await reply(
            update,
            f"Ок, поставил временную дату форума: <b>{forum_date.strftime('%d.%m.%Y')}</b>. "
            "Её можно поменять в личном кабинете.",
            reply_markup=MAIN_KEYBOARD,
        )
        await notify_admin_new_user(context, user)
        await show_profile_cabinet(update, user)


async def notify_admin_new_user(context: ContextTypes.DEFAULT_TYPE, user: dict[str, Any]) -> None:
    if not ADMIN_CHAT_ID or user.get("admin_notified"):
        return
    ok = await safe_send(
        context,
        ADMIN_CHAT_ID,
        "<b>Новый пользователь ForumUpdateHelperBot</b>\n"
        f"ФИ: <b>{esc(user.get('full_name'))}</b>\n"
        f"Бизнес-клуб: <b>{esc(user.get('business_club'))}</b>",
    )
    if ok:
        store.update_user(user["telegram_user_id"], admin_notified=1)


async def handle_next_forum_date(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    text: str,
) -> None:
    forum_date = parse_forum_date(text)
    if forum_date is None:
        await reply(update, "Не распознал дату. Попробуй так: <code>23.06.2026</code>.")
        return
    user = store.update_user(user["telegram_user_id"], next_forum_date=forum_date.isoformat(), state=None)
    await reply(
        update,
        f"Запомнил: следующий форум <b>{forum_date.strftime('%d.%m.%Y')}</b>.\n\n"
        "Напомню об апдейте заранее и на следующее утро после форума спрошу здоровье группы.",
        reply_markup=MAIN_KEYBOARD,
    )
    if is_profile_complete(user):
        await notify_admin_new_user(context, user)


def profile_cabinet_text(user: dict[str, Any]) -> str:
    report_recipient = (user.get("community_chat") or "").strip()
    methodology = methodology_for_user(user)
    keep_files = (
        "сохранять апдейты и документы; голосовые удалять сразу"
        if user.get("keep_files")
        else "удалять после обработки"
    )
    diary = "включён" if user.get("diary_enabled") else "выключен"
    forum_date = user.get("next_forum_date") or "не указана"
    recipient_text = report_recipient or "не указан — отчёты остаются в личном чате"
    return (
        "<b>Личный кабинет</b>\n\n"
        f"Бизнес-клуб: <b>{esc(user.get('business_club') or 'не указан')}</b>\n"
        f"ФИ: <b>{esc(user.get('full_name') or 'не указано')}</b>\n"
        f"Форум-группа: <b>{esc(user.get('forum_group') or 'не указана')}</b>\n"
        f"Методика: <b>{esc(methodology)}</b>\n"
        f"Получатель отчётов: <b>{esc(recipient_text)}</b>\n"
        f"Файлы: <b>{esc(keep_files)}</b>\n"
        f"Следующий форум: <b>{esc(forum_date)}</b>\n"
        f"Режим дневника: <b>{esc(diary)}</b>\n\n"
        "Все поля можно изменить здесь."
    )


def profile_cabinet_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Бизнес-клуб", callback_data="profile:edit:business_club"),
                InlineKeyboardButton("ФИ", callback_data="profile:edit:full_name"),
            ],
            [
                InlineKeyboardButton("Форум-группа", callback_data="profile:edit:forum_group"),
                InlineKeyboardButton("Методика", callback_data="profile:edit:methodology"),
            ],
            [InlineKeyboardButton("Получатель отчётов", callback_data="profile:edit:community_chat")],
            [
                InlineKeyboardButton("Файлы", callback_data="profile:edit:keep_files"),
                InlineKeyboardButton("Дата форума", callback_data="profile:edit:next_forum_date"),
            ],
            [
                InlineKeyboardButton("Режим дневника", callback_data="diary:mode"),
                InlineKeyboardButton("Промпт дневника", callback_data="diary:prompt"),
            ],
        ]
    )


async def show_profile_cabinet(update: Update, user: dict[str, Any]) -> None:
    fresh = store.get_user(user["telegram_user_id"]) or user
    await reply(update, profile_cabinet_text(fresh), reply_markup=profile_cabinet_keyboard())


async def start_profile_edit(update: Update, user: dict[str, Any], field: str) -> None:
    if field == "business_club":
        store.update_user(user["telegram_user_id"], state=None)
        await reply(update, "Выбери бизнес-клуб.", reply_markup=business_club_keyboard(prefix="profile:club"))
        return
    if field == "methodology":
        store.update_user(user["telegram_user_id"], state=None)
        await reply(update, "Выбери методику подготовки апдейта.", reply_markup=methodology_keyboard(prefix="profile:methodology"))
        return
    if field == "keep_files":
        store.update_user(user["telegram_user_id"], state=None)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Сохранять", callback_data="profile:keep:1"),
                    InlineKeyboardButton("Удалять", callback_data="profile:keep:0"),
                ]
            ]
        )
        await reply(update, "Как поступать с файлами после обработки?", reply_markup=keyboard)
        return
    if field == "community_chat":
        store.update_user(user["telegram_user_id"], state="profile:community_chat")
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("Очистить", callback_data="profile:clear_community")]])
        await reply(
            update,
            "Пришли Telegram username пользователя, например <code>@utandr</code>. "
            "Бот отправит ему отчёт, только если этот пользователь уже запускал бота. "
            "Если очистить поле, отчёты останутся в личном чате.",
            reply_markup=keyboard,
        )
        return
    if field == "next_forum_date":
        store.update_user(user["telegram_user_id"], state="profile:next_forum_date")
        await reply(update, "Пришли новую дату форума: например, <code>23.06.2026</code>.")
        return
    prompts = {
        "full_name": "Напиши новые Фамилию Имя.",
        "forum_group": "Напиши новое название форум-группы.",
    }
    if field not in prompts:
        await reply(update, "Не понял, какое поле изменить.")
        return
    store.update_user(user["telegram_user_id"], state=f"profile:{field}")
    await reply(update, prompts[field])


async def handle_profile_edit_text(update: Update, user: dict[str, Any], text: str) -> None:
    field = str(user.get("state") or "").removeprefix("profile:")
    value = text.strip()
    if not value:
        await reply(update, "Пустое значение не записал. Пришли новое значение.")
        return

    if field == "full_name":
        updated = store.update_user(user["telegram_user_id"], full_name=value[:160], state=None)
    elif field == "forum_group":
        updated = store.update_user(user["telegram_user_id"], forum_group=value[:160], state=None)
    elif field == "community_chat":
        recipient = normalize_report_recipient(value)
        if not recipient:
            await reply(update, "Пришли Telegram username в формате <code>@username</code> или нажми «Очистить».")
            return
        updated = store.update_user(user["telegram_user_id"], community_chat=recipient, state=None)
    elif field == "next_forum_date":
        forum_date = parse_forum_date(value)
        if forum_date is None:
            await reply(update, "Не распознал дату. Попробуй так: <code>23.06.2026</code>.")
            return
        updated = store.update_user(user["telegram_user_id"], next_forum_date=forum_date.isoformat(), state=None)
    else:
        await reply(update, "Не понял, какое поле изменить.")
        return

    await reply(update, "Сохранил.")
    await show_profile_cabinet(update, updated)


async def show_forum_guide(update: Update, user: dict[str, Any]) -> None:
    methodology = methodology_for_user(user)
    store.update_user(user["telegram_user_id"], state=None)
    await reply(
        update,
        "<b>Справочник форума</b>\n\n"
        "Я сохранил материалы из фото: общие принципы форума, формулу общения, "
        "правило 5%, окно Джохари, список чувств и классический Update.\n\n"
        f"Твоя текущая методика апдейта: <b>{esc(methodology)}</b>.\n"
        "Можно спросить, например: «Можно ли это выносить на форум?» или "
        "«Оцени мой апдейт по методике».",
        reply_markup=guide_keyboard(),
    )


async def start_guide_question(update: Update, user: dict[str, Any]) -> None:
    store.update_user(user["telegram_user_id"], state="guide:question", active_flow=None)
    await reply(
        update,
        "<b>Вопрос по справочнику</b>\n\n"
        "Напиши вопрос или пришли апдейт, который надо оценить по материалам форума.",
        reply_markup=cancel_keyboard(),
    )


async def answer_guide_question(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    text: str,
) -> None:
    store.update_user(user["telegram_user_id"], state=None)
    await reply(update, "Сверяю со справочником форума.")
    answer = await generate_guide_answer(user, text)
    await reply(update, answer, reply_markup=MAIN_KEYBOARD)


async def generate_guide_answer(user: dict[str, Any], question: str) -> str:
    methodology = methodology_for_user(user)
    guide_context = load_forum_guide_context(methodology)
    if _openai is None:
        return (
            "OpenAI не настроен на сервере, поэтому сейчас могу только подсказать, "
            "что справочник уже сохранён в материалах бота. Ключевые опоры: говорить "
            "только из личного опыта, не давать советов, выносить в форум 5% самых "
            "важных радостных и тяжёлых тем, держать конфиденциальность."
        )

    system = (
        "Ты Telegram-ассистент форум-группы. Отвечай на русском, коротко и практично. "
        "Опирайся только на переданный справочник. Не выдумывай правил, если их нет в контексте. "
        "Если пользователь просит оценить апдейт, оцени глубину, соответствие методике, "
        "личный опыт, чувства, отсутствие советов и качество главного вопроса."
    )
    prompt = (
        f"Методика пользователя: {methodology}\n\n"
        f"Справочник:\n{guide_context}\n\n"
        f"Вопрос пользователя:\n{question[:12000]}\n\n"
        "Ответь в формате:\n"
        "1. Короткий вывод\n"
        "2. Что из справочника важно применить\n"
        "3. Один следующий шаг"
    )

    def _call() -> str:
        response = _openai.responses.create(
            model=OPENAI_MODEL,
            instructions=system,
            input=prompt,
            max_output_tokens=900,
            text={"verbosity": "low"},
        )
        return extract_response_text(response)

    try:
        return await asyncio.to_thread(_call)
    except Exception as exc:
        log.warning("guide answer failed user_id=%s error=%s", user.get("telegram_user_id"), exc)
        return "Не смог ответить по справочнику сейчас. Попробуй ещё раз позже."


async def show_diary_mode_menu(update: Update, user: dict[str, Any]) -> None:
    enabled = bool(user.get("diary_enabled"))
    prompt = (user.get("diary_feedback_prompt") or "").strip()
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Выключить" if enabled else "Включить",
                    callback_data="diary:disable" if enabled else "diary:enable",
                )
            ],
            [InlineKeyboardButton("Поменять prompt обратной связи", callback_data="diary:prompt")],
        ]
    )
    status = "включён" if enabled else "выключен"
    prompt_text = prompt or "пока не задан"
    await reply(
        update,
        f"<b>Режим дневника: {status}</b>\n\n"
        "Когда режим включён, свободные сообщения вне апдейта и health check "
        "я воспринимаю как дневниковые записи и даю обратную связь по твоему prompt.\n\n"
        f"<b>Текущий prompt</b>\n{esc(prompt_text)}",
        reply_markup=keyboard,
    )


async def start_diary_prompt_setup(update: Update, user: dict[str, Any], enable: bool) -> None:
    store.update_user(user["telegram_user_id"], state="diary:prompt")
    verb = "включить" if enable else "изменить"
    await reply(
        update,
        f"<b>Режим дневника</b>\n\n"
        f"Напиши, как именно мне давать обратную связь на дневник. "
        f"Например:\n\n"
        f"<i>С точки зрения лидерства, Алмазного огранщика и моих паттернов.</i>\n\n"
        f"После этого я {verb} режим дневника.",
        reply_markup=cancel_keyboard(),
    )


async def save_diary_prompt(update: Update, user: dict[str, Any], text: str) -> None:
    prompt = text.strip()[:2000]
    if not prompt:
        await reply(update, "Prompt пустой. Напиши, какую обратную связь давать на дневник.")
        return
    store.update_user(
        user["telegram_user_id"],
        diary_enabled=1,
        diary_feedback_prompt=prompt,
        state=None,
    )
    await reply(
        update,
        "<b>Режим дневника включён</b>\n\n"
        "Теперь свободные сообщения буду читать как дневник и давать обратную связь так:\n"
        f"{esc(prompt)}",
        reply_markup=MAIN_KEYBOARD,
    )


async def handle_diary_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    text: str,
) -> None:
    prompt = (user.get("diary_feedback_prompt") or "").strip()
    if not prompt:
        await start_diary_prompt_setup(update, user, enable=True)
        return
    store.log_interaction(user["telegram_user_id"], "diary_entry")
    await reply(update, "Принял как дневниковую запись. Дам обратную связь.")
    feedback = await generate_diary_feedback(user, text, prompt)
    await reply(update, feedback or "Не смог собрать обратную связь сейчас. Запись принял.", reply_markup=MAIN_KEYBOARD)


async def generate_diary_feedback(user: dict[str, Any], entry: str, feedback_prompt: str) -> str:
    if _openai is None:
        return (
            "OpenAI не настроен на сервере, поэтому AI-обратная связь недоступна. "
            "Режим дневника включён; запись можно отправить позже после настройки ключа."
        )

    system = (
        "Ты Telegram-ассистент дневниковой рефлексии для предпринимателей. "
        "Давай короткую, практичную обратную связь на русском. "
        "Не ставь диагнозов, не давай медицинских или юридических советов. "
        "Работай как зеркало: наблюдения, вопросы, паттерны, следующий маленький шаг."
    )
    prompt = (
        f"Пользователь: {user.get('full_name') or 'не указан'}\n"
        f"Форум-группа: {user.get('forum_group') or 'не указана'}\n\n"
        f"Как давать обратную связь:\n{feedback_prompt}\n\n"
        f"Дневниковая запись:\n{entry[:12000]}\n\n"
        "Формат ответа:\n"
        "1. Что я слышу\n"
        "2. Возможный паттерн\n"
        "3. Вопрос к себе\n"
        "4. Один следующий шаг"
    )

    def _call() -> str:
        response = _openai.responses.create(
            model=OPENAI_MODEL,
            instructions=system,
            input=prompt,
            max_output_tokens=900,
            text={"verbosity": "low"},
        )
        return extract_response_text(response)

    try:
        return await asyncio.to_thread(_call)
    except Exception as exc:
        log.warning("diary feedback failed user_id=%s error=%s", user.get("telegram_user_id"), exc)
        return ""


async def start_update_flow(update: Update, user: dict[str, Any]) -> None:
    if not is_profile_complete(user):
        await start_onboarding(update, None, user)  # type: ignore[arg-type]
        return
    methodology = methodology_for_user(user)
    questions = update_questions_for_user(user)
    user = store.set_flow(user["telegram_user_id"], "update", 0, {"answers": {}})
    await reply(
        update,
        f"<b>Начинаем апдейт: {esc(methodology)}</b>\n\n"
        "Будем идти по всем вопросам. Отвечай коротко или голосом. "
        "В конце я соберу Markdown-файл апдейта.",
        reply_markup=cancel_keyboard(),
    )
    await ask_current_question(update, user, questions)


async def start_health_flow(update: Update, user: dict[str, Any]) -> None:
    if not is_profile_complete(user):
        await start_onboarding(update, None, user)  # type: ignore[arg-type]
        return
    user = store.set_flow(user["telegram_user_id"], "health", 0, {"answers": {}})
    await reply(
        update,
        "<b>Health check форум-группы</b>\n\n"
        "Отвечай честно и конкретно. В конце я соберу отчёт и попробую отправить "
        "его указанному Telegram-пользователю, если он уже запускал бота.",
        reply_markup=cancel_keyboard(),
    )
    await ask_current_question(update, user, HEALTH_QUESTIONS)


def question_message(question: Question, step: int, total: int) -> str:
    return (
        f"<b>{esc(question.section)}</b>\n"
        f"Заполнено: <b>{step}/{total}</b>\n"
        f"Вопрос {step + 1}/{total}\n\n"
        f"{esc(question.prompt)}"
    )


async def ask_current_question(
    update: Update,
    user: dict[str, Any],
    questions: list[Question],
) -> None:
    step = int(user.get("active_step") or 0)
    if step >= len(questions):
        return
    question = questions[step]
    store.update_user(user["telegram_user_id"], state=None)
    await reply(
        update,
        question_message(question, step, len(questions)),
        reply_markup=flow_keyboard(False),
    )


async def handle_flow_next(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
) -> None:
    flow = user.get("active_flow")
    if flow == "update":
        questions = update_questions_for_user(user)
    elif flow == "health":
        questions = HEALTH_QUESTIONS
    else:
        await reply(update, "Активного сценария нет. Выбери действие в меню.", reply_markup=MAIN_KEYBOARD)
        return
    if user.get("state") != "flow:await_next":
        await ask_current_question(update, user, questions)
        return
    user = store.update_user(user["telegram_user_id"], state=None)
    if int(user.get("active_step") or 0) >= len(questions):
        await finish_flow(update, context, user)
        return
    await ask_current_question(update, user, questions)


async def handle_question_answer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    text: str,
    questions: list[Question],
) -> None:
    step = int(user.get("active_step") or 0)
    payload = store.payload(user)
    answers = payload.setdefault("answers", {})
    if step >= len(questions):
        await finish_flow(update, context, user)
        return

    question = questions[step]
    answers[question.key] = text
    next_step = step + 1
    user = store.update_user(
        user["telegram_user_id"],
        active_step=next_step,
        flow_payload=json.dumps(payload, ensure_ascii=False),
        state="flow:await_next",
    )
    if next_step >= len(questions):
        await finish_flow(update, context, user)
        return

    await reply(
        update,
        f"Записал. Заполнено: <b>{next_step}/{len(questions)}</b>.",
        reply_markup=flow_keyboard(True),
    )


async def finish_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
) -> None:
    flow = user.get("active_flow")
    payload = store.payload(user)
    answers = payload.get("answers", {})
    if flow == "update":
        await finish_update_flow(update, context, user, answers)
    elif flow == "health":
        await finish_health_flow(update, context, user, answers)
    store.set_flow(user["telegram_user_id"], None)


def answers_by_section(answers: dict[str, str], questions: list[Question]) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for question in questions:
        grouped.setdefault(question.section, []).append((question.prompt, answers.get(question.key, "")))
    return grouped


def build_update_markdown(
    user: dict[str, Any],
    answers: dict[str, str],
    reflection: str = "",
    questions: list[Question] | None = None,
) -> str:
    forum_date = user.get("next_forum_date") or "не указана"
    methodology = methodology_for_user(user)
    selected_questions = questions or update_questions_for_user(user)
    lines = [
        f"# Форум-апдейт — {user.get('forum_group') or 'форум-группа'}",
        "",
        f"- Участник: {user.get('full_name') or ''}",
        f"- Бизнес-клуб: {user.get('business_club') or ''}",
        f"- Методика: {methodology}",
        f"- Дата форума: {forum_date}",
        f"- Создано: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    for section, items in answers_by_section(answers, selected_questions).items():
        lines.extend([f"## {section}", ""])
        for prompt, answer in items:
            lines.extend([f"**{prompt}**", "", answer.strip() or "_Нет ответа_", ""])
    if reflection:
        lines.extend(["## Короткая менторская сводка", "", reflection.strip(), ""])
    return "\n".join(lines).strip() + "\n"


async def maybe_reflect_update(
    user: dict[str, Any],
    answers: dict[str, str],
    questions: list[Question],
) -> str:
    if not (_openai and OPENAI_REFLECTION_ENABLED):
        return ""

    compact_answers = "\n".join(
        f"- {q.prompt}: {answers.get(q.key, '')}" for q in questions if answers.get(q.key)
    )
    methodology = methodology_for_user(user)
    guide_context = load_forum_guide_context(methodology, max_chars=10000)
    prompt = (
        "Ты MCC-level коуч и бизнес-ментор. Дай короткую сводку форумного "
        "апдейта на русском. Оцени подготовку через выбранную методику и "
        "принципы форума: личный опыт, чувства, глубина 5%, отсутствие советов, "
        "ясность главного вопроса. Без советов группе, только подготовка автора.\n\n"
        f"Методика: {methodology}\n\n"
        f"Справочник:\n{guide_context}\n\n"
        f"Апдейт:\n{compact_answers[:16000]}"
    )

    def _call() -> str:
        response = _openai.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
            max_output_tokens=900,
            text={"verbosity": "low"},
        )
        return extract_response_text(response)

    try:
        return await asyncio.to_thread(_call)
    except Exception as exc:
        log.warning("OpenAI reflection failed: %s", exc)
        return ""


def extract_response_text(response: Any) -> str:
    text = getattr(response, "output_text", None)
    if text:
        return str(text).strip()
    chunks: list[str] = []
    for item in getattr(response, "output", []) or []:
        content_items = item.get("content", []) if isinstance(item, dict) else getattr(item, "content", [])
        for content in content_items or []:
            value = content.get("text") if isinstance(content, dict) else getattr(content, "text", None)
            if value:
                chunks.append(str(value))
    return "\n".join(chunks).strip()


async def finish_update_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    answers: dict[str, str],
) -> None:
    await reply(update, "Собираю апдейт в Markdown. Если включена AI-сводка, добавлю короткую менторскую выжимку.")
    questions = update_questions_for_user(user)
    reflection = await maybe_reflect_update(user, answers, questions)
    content = build_update_markdown(user, answers, reflection, questions)
    user_dir = UPDATES_DIR / str(user["telegram_user_id"])
    user_dir.mkdir(parents=True, exist_ok=True)
    filename = f"forum-update-{datetime.now(TZ).strftime('%Y%m%d-%H%M')}.md"
    path = user_dir / filename
    path.write_text(content, encoding="utf-8")
    if update.effective_message and update.effective_chat:
        with path.open("rb") as fh:
            await spaced_bot_send(
                update.effective_chat.id,
                lambda: update.effective_message.reply_document(
                    document=fh,
                    filename=filename,
                    caption=f"Готово: форумный апдейт {methodology_for_user(user)}.",
                    reply_markup=MAIN_KEYBOARD,
                ),
            )
    if not user.get("keep_files"):
        path.unlink(missing_ok=True)
    await reply(update, "После форума я спрошу здоровье группы на следующее утро.", reply_markup=MAIN_KEYBOARD)


def build_health_report(user: dict[str, Any], answers: dict[str, str]) -> str:
    lines = [
        f"<b>Здоровье форум-группы: {esc(user.get('forum_group'))}</b>",
        f"Участник: {esc(user.get('full_name'))}",
        f"Бизнес-клуб: {esc(user.get('business_club'))}",
        f"Дата отчёта: {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}",
        "",
    ]
    for question in HEALTH_QUESTIONS:
        lines.extend([f"<b>{esc(question.prompt)}</b>", esc(answers.get(question.key, "Нет ответа")), ""])
    return "\n".join(lines).strip()


def resolve_report_recipient(username: str) -> dict[str, Any] | None:
    return store.get_user_by_username(username)


async def finish_health_flow(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    answers: dict[str, str],
) -> None:
    report = build_health_report(user, answers)
    await reply(update, report, reply_markup=MAIN_KEYBOARD)
    report_recipient = (user.get("community_chat") or "").strip()
    if report_recipient:
        recipient = resolve_report_recipient(report_recipient)
        if recipient is None:
            await reply(
                update,
                f"Не нашёл пользователя <b>{esc(report_recipient)}</b> среди тех, кто уже запускал бота. "
                "Отчёт выше можно переслать вручную или попросить пользователя сначала отправить /start боту.",
            )
            await ask_next_forum_date(update, user)
            return
        ok = await safe_send(context, int(recipient["chat_id"]), report)
        if ok:
            await reply(update, f"Отправил отчёт пользователю <b>@{esc(recipient.get('username'))}</b>.")
        else:
            await reply(
                update,
                "Не смог отправить отчёт этому пользователю. Возможно, он остановил бота. "
                "Отчёт выше можно переслать вручную.",
            )
    await ask_next_forum_date(update, user)


async def ask_delete_data(update: Update) -> None:
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да, удалить", callback_data="delete:confirm"),
                InlineKeyboardButton("Отмена", callback_data="delete:cancel"),
            ]
        ]
    )
    await reply(
        update,
        "<b>Удалить мои данные с сервера?</b>\n\n"
        "Я удалю профиль, даты форума, состояние сценариев, историю напоминаний, "
        "счётчики обращений и сохранённые файлы. Уже отправленные сообщения в Telegram "
        "удалить не смогу.",
        reply_markup=keyboard,
    )


async def delete_my_data(update: Update, user: dict[str, Any]) -> None:
    store.delete_user(user["telegram_user_id"])
    await reply(update, "Готово. Данные удалены с сервера. Чтобы начать заново, отправь /start.")


async def handle_voice_or_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "audio")
    if _openai is None:
        await reply(update, "Голос сейчас недоступен: на сервере не задан OPENAI_API_KEY. Ответь текстом.")
        return
    message = update.effective_message
    if message is None:
        return
    await message.chat.send_action(ChatAction.TYPING)
    tg_file = None
    original_name = "voice.ogg"
    if message.voice:
        tg_file = await message.voice.get_file()
        original_name = "voice.ogg"
    elif message.audio:
        tg_file = await message.audio.get_file()
        original_name = message.audio.file_name or "audio.ogg"
    if tg_file is None:
        return

    suffix = Path(original_name).suffix.lower()
    if suffix in {"", ".oga", ".opus"}:
        suffix = ".ogg"

    tmp = tempfile.NamedTemporaryFile(prefix="forum-audio-", suffix=suffix, delete=False)
    tmp.close()
    audio_path = Path(tmp.name)

    try:
        await tg_file.download_to_drive(custom_path=str(audio_path))
        transcript = await transcribe_audio(audio_path)
    except Exception as exc:
        log.exception("audio transcription failed")
        await reply(
            update,
            "Не смог распознать голос. Обработчик: OpenAI transcription, "
            f"тип файла: {esc(suffix)}, ошибка: {esc(type(exc).__name__)}: {esc(exc)}. "
            "Можно ответить текстом.",
        )
        return
    finally:
        audio_path.unlink(missing_ok=True)

    await reply(update, f"<b>Транскрипт</b>\n{esc(transcript)}")
    fresh_user = store.get_user(user["telegram_user_id"]) or user
    await route_text(update, context, fresh_user, transcript)


async def transcribe_audio(path: Path) -> str:
    assert _openai is not None

    def _call() -> str:
        with path.open("rb") as fh:
            result = _openai.audio.transcriptions.create(
                model=TRANSCRIBE_MODEL,
                file=fh,
                language=TRANSCRIBE_LANGUAGE,
                prompt=(
                    "Русская речь участника бизнес-форума. Термины: форум-группа, "
                    "X-Competence, апдейт, Атланты, Эквиум, К1, Терра, Сколково."
                ),
            )
        return str(getattr(result, "text", "")).strip()

    return await asyncio.to_thread(_call)


async def handle_document(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "document")
    message = update.effective_message
    if not message or not message.document:
        return
    tg_file = await message.document.get_file()
    filename = re.sub(r"[^A-Za-z0-9А-Яа-яЁё._-]+", "_", message.document.file_name or "file")
    if user.get("keep_files"):
        target_dir = UPLOADS_DIR / str(user["telegram_user_id"])
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{datetime.now(TZ).strftime('%Y%m%d-%H%M%S')}-{filename}"
    else:
        tmp = tempfile.NamedTemporaryFile(prefix="forum-file-", suffix=Path(filename).suffix, delete=False)
        tmp.close()
        path = Path(tmp.name)
    try:
        await tg_file.download_to_drive(custom_path=str(path))
        if user.get("keep_files"):
            await reply(update, "Файл скачал и сохранил по твоей настройке.")
        else:
            await reply(update, "Файл скачал для обработки и удалил по твоей настройке.")
    finally:
        if not user.get("keep_files"):
            path.unlink(missing_ok=True)


async def daily_maintenance(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now(TZ).date()
    for user in store.complete_users():
        if not is_profile_complete(user):
            continue
        try:
            await maybe_send_forum_reminders(context, user, today)
            await maybe_send_offsite_reminder(context, user, today)
        except Exception:
            log.exception("daily maintenance failed for user_id=%s", user.get("telegram_user_id"))


async def maybe_send_forum_reminders(
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    today: date,
) -> None:
    value = user.get("next_forum_date")
    if not value:
        return
    forum_date = date.fromisoformat(value)
    days_left = (forum_date - today).days
    chat_id = int(user["chat_id"])

    if days_left in PRE_FORUM_REMINDER_DAYS:
        reminder_type = f"pre_forum_{days_left}"
        if not store.reminder_sent(user["telegram_user_id"], reminder_type, value):
            fresh = store.get_user(user["telegram_user_id"]) or user
            if not fresh.get("active_flow"):
                questions = update_questions_for_user(fresh)
                methodology = methodology_for_user(fresh)
                store.set_flow(user["telegram_user_id"], "update", 0, {"answers": {}})
                await safe_send(
                    context,
                    chat_id,
                    "<b>Пора готовить форумный апдейт</b>\n\n"
                    f"До форума {days_left} дн. Начинаю подготовку по методике {esc(methodology)}. "
                    "Отвечай текстом или голосом.",
                )
                await safe_send(
                    context,
                    chat_id,
                    question_message(questions[0], 0, len(questions)),
                    reply_markup=flow_keyboard(False),
                )
            else:
                await safe_send(
                    context,
                    chat_id,
                    "<b>Пора готовить форумный апдейт</b>\n\n"
                    "У тебя уже открыт другой сценарий. Закончи его или нажми /cancel, "
                    "потом выбери «Подготовить апдейт».",
                )
            store.mark_reminder(user["telegram_user_id"], reminder_type, value)

    if today == forum_date + timedelta(days=1):
        if not store.reminder_sent(user["telegram_user_id"], "post_forum_health", value):
            fresh = store.get_user(user["telegram_user_id"]) or user
            if not fresh.get("active_flow"):
                store.set_flow(user["telegram_user_id"], "health", 0, {"answers": {}})
                await safe_send(
                    context,
                    chat_id,
                    "<b>Утро после форума</b>\n\n"
                    "Давай зафиксируем здоровье форум-группы. Первый вопрос:",
                )
                await safe_send(
                    context,
                    chat_id,
                    question_message(HEALTH_QUESTIONS[0], 0, len(HEALTH_QUESTIONS)),
                    reply_markup=flow_keyboard(False),
                )
            else:
                await safe_send(
                    context,
                    chat_id,
                    "Сегодня нужно пройти health check форум-группы. Закончи текущий сценарий или нажми /cancel.",
                )
            store.mark_reminder(user["telegram_user_id"], "post_forum_health", value)


async def maybe_send_offsite_reminder(
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    today: date,
) -> None:
    last_value = user.get("last_offsite_reminder_date") or user.get("created_at", "")[:10]
    try:
        last_date = date.fromisoformat(last_value)
    except ValueError:
        last_date = today
    if (today - last_date).days < OFFSITE_INTERVAL_DAYS:
        return
    reminder_key = today.isoformat()
    if store.reminder_sent(user["telegram_user_id"], "offsite", reminder_key):
        return
    await safe_send(
        context,
        int(user["chat_id"]),
        "<b>Квартальный выезд на личную стратсессию</b>\n\n"
        "Рекомендую забронировать 1-2 дня в отеле вне города. Хороший вариант: "
        "тихий загородный отель 4-5*, спа/баня, место для прогулки, нормальный стол, "
        "без плотной социальной программы.\n\n"
        "Фокус сессии: итоги квартала, 3 сферы, один главный запрос, решения на 90 дней.",
    )
    store.mark_reminder(user["telegram_user_id"], "offsite", reminder_key)
    store.update_user(user["telegram_user_id"], last_offsite_reminder_date=today.isoformat())


async def post_init(application: Application) -> None:
    maintenance_time = parse_time(DAILY_MAINTENANCE_TIME)
    application.job_queue.run_daily(daily_maintenance, time=maintenance_time, name="daily-maintenance")
    application.job_queue.run_once(daily_maintenance, when=10, name="startup-maintenance")
    log.info("ForumUpdateHelperBot started")


def build_application() -> Application:
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("about", cmd_about))
    app.add_handler(CommandHandler("forum", cmd_prepare))
    app.add_handler(CommandHandler("update", cmd_prepare))
    app.add_handler(CommandHandler("nextforum", cmd_next_forum))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("guide", cmd_guide))
    app.add_handler(CommandHandler("profile", cmd_profile))
    app.add_handler(CommandHandler("cabinet", cmd_profile))
    app.add_handler(CommandHandler("diary", cmd_diary))
    app.add_handler(CommandHandler("diaryprompt", cmd_diary))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("getid", cmd_getid))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_or_audio))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    return app


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    app = build_application()
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
