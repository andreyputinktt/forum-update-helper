#!/usr/bin/env python3
"""ForumUpdateHelperBot: Telegram assistant for X-Competence forum updates."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
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
BOT_USERNAME = os.getenv("BOT_USERNAME", "ForumUpdateHelperBot").removeprefix("@")
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
UTF8_BOM = b"\xef\xbb\xbf"
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5")
OPENAI_HTML_MODEL = os.getenv("OPENAI_HTML_MODEL", OPENAI_MODEL)
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
METHODOLOGY_CALLBACKS = {
    "classic": METHODOLOGY_CLASSIC,
    "strategy": METHODOLOGY_STRATEGY,
}
DEFAULT_METHODOLOGY = METHODOLOGY_CLASSIC
ONBOARDING_TOTAL_STEPS = 9
AI_AGENT_MD_CHOICE = "Сложно: загрузить только файл"
AI_AGENT_BOT_CHOICE = "Просто: продолжить работу"
DEFAULT_DIARY_PROMPT = "С точки зрения лидерства, Алмазного огранщика и моих паттернов."
DIARY_REMINDER_CHOICES = {
    "21": ("21:00", "в 21:00 этого дня"),
    "08": ("08:00", "в 08:00 следующего дня"),
}
FORUM_GUIDE_DIR = BASE_DIR / "docs" / "forum-guide"
COMMON_GUIDE_PATH = FORUM_GUIDE_DIR / "forum-common-guide.md"
CLASSIC_GUIDE_PATH = FORUM_GUIDE_DIR / "classic-update.md"
X_COMPETENCE_GUIDE_PATH = FORUM_GUIDE_DIR / "x-competence-update.md"

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["Апдейты"],
        ["Моя форум-группа", "Дневник"],
        ["Личный кабинет", "Доп. информация"],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

PSYCHOLOGIST_URL = "https://all.achernigova.ru/p/recommendediarpt/"
COACH_URL = "https://5prism.ru/kouchi/"
REPO_URL = "https://github.com/andreyputinktt/forum-update-helper"
BUILD_BOT_DOC_URL = "https://github.com/andreyputinktt/forum-update-helper/blob/main/CREATE_OWN_BOT.md"
UPDATE_MD_CAPTION = "Форумный апдейт в .md для ИИ."
UPDATE_HTML_CAPTION = "Форумный апдейт в .html для чтения на форуме."
AUTHOR_TEXT = (
    "Автор бота: Андрей Путин.\n"
    "Telegram: @utandr\n\n"
    "Можно писать, если хотите связаться с автором, предложить улучшение или "
    "стать соавтором. Бот развёрнут на сервере компании kt.team."
)
ABOUT_DEEPLINK = f"https://t.me/{BOT_USERNAME}?start=about"


@dataclass(frozen=True)
class Question:
    key: str
    prompt: str
    section: str


SPHERES = ("Моё дело", "Моя семья / близкие", "Я")
RATING_7_WARNING = (
    "Важно: оценка 7 коварная, мы не рекомендуем её использовать — она находится "
    "между «хорошо» и «средне» и часто становится слишком нейтральным ответом."
)

def x_competence_rating_prompt(sphere: str) -> str:
    return (
        f"{sphere}: дай оценку месяца.\n\n"
        "Укажи:\n"
        "- оценку этого месяца: 1-10;\n"
        "- оценку предыдущего месяца: 1-10;\n"
        "- что изменилось;\n"
        "- что дало наибольший вклад в эту оценку;\n"
        "- если оценка упала: что произошло;\n"
        "- если выросла: за счёт чего.\n\n"
        f"{RATING_7_WARNING}"
    )


def classic_rating_prompt(sphere: str) -> str:
    return f"{sphere}: поставь оценку месяца 1-10.\n\n{RATING_7_WARNING}"


UPDATE_QUESTIONS: list[Question] = []
for sphere in SPHERES:
    UPDATE_QUESTIONS.extend(
        [
            Question(
                f"rating_{sphere}",
                x_competence_rating_prompt(sphere),
                "Часть 1. Оценка трёх сфер",
            ),
        ]
    )

for sphere in SPHERES:
    UPDATE_QUESTIONS.extend(
        [
            Question(
                f"retrospective_{sphere}",
                f"{sphere}: собери ретроспективу периода.\n\n"
                "- Что ты планировал на прошедший период?\n"
                "- Что получил по факту?\n"
                "- Какое действие дало максимальный результат?\n"
                "- Что не сработало и почему?",
                "Часть 2. Ретроспектива",
            ),
            Question(
                f"next_period_{sphere}",
                f"{sphere}: опиши следующий период.\n\n"
                "- Что будет означать «отлично» через месяц?\n"
                "- Какая годовая цель и как сегодняшний запрос с ней связан?\n"
                "- Что в твоей власти?\n"
                "- Что вне твоего контроля?\n"
                "- Что поддерживает?\n"
                "- Что мешает?",
                "Часть 2. Следующий период",
            ),
        ]
    )

UPDATE_QUESTIONS.extend(
    [
        Question(
            "main_request_core",
            "Сформулируй главный запрос и коротко объясни контекст.\n\n"
            "Запрос:\n"
            "- один вопрос;\n"
            "- до 10 слов;\n"
            "- начинается с «Как мне ... ?»;\n"
            "- находится в зоне твоего контроля.\n\n"
            "Контекст:\n"
            "- почему это важно;\n"
            "- на какие сферы влияет.",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_money",
            "Оцени денежный эквивалент проблемы.\n\n"
            "Укажи:\n"
            "- ущерб или упущенную прибыль в год;\n"
            "- сколько ты готов заплатить за лучшее решение.",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_goal_history",
            "Опиши идеальный результат и историю ситуации.\n\n"
            "Ответь:\n"
            "- как выглядит идеально разрешённая ситуация;\n"
            "- как и когда она начала развиваться;\n"
            "- что будет, если ничего не менять.",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_attempts",
            "Что уже пробовал и какие варианты видишь сейчас?\n\n"
            "Укажи:\n"
            "- что уже делал;\n"
            "- минимум 3 варианта решения.",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_choice",
            "Выбери рабочую гипотезу.\n\n"
            "Ответь:\n"
            "- если бы не обсуждал с группой, как бы действовал?\n"
            "- что останавливает от реализации этого решения?",
            "Часть 3. Главный запрос",
        ),
        Question(
            "main_request_help",
            "Какая помощь нужна от группы?\n\n"
            "Начни фразой: «Поделитесь опытом, как вы...»",
            "Часть 3. Главный запрос",
        ),
    ]
)

CLASSIC_UPDATE_QUESTIONS = [
    Question(
        "classic_business_rating",
        classic_rating_prompt("Бизнес"),
        "Классический Update. Оценки месяца",
    ),
    Question(
        "classic_family_rating",
        classic_rating_prompt("Семья"),
        "Классический Update. Оценки месяца",
    ),
    Question(
        "classic_personal_rating",
        classic_rating_prompt("Личное"),
        "Классический Update. Оценки месяца",
    ),
]

for sphere in ("Бизнес", "Семья", "Личное"):
    for sign, label in (("plus", "плюс"), ("minus", "минус")):
        CLASSIC_UPDATE_QUESTIONS.extend(
            [
                Question(
                    f"classic_{sphere}_{sign}_event",
                    f"{sphere}, {label}.\n\nСамое важное, что произошло. Одним предложением.",
                    f"Классический Update. {sphere}",
                ),
                Question(
                    f"classic_{sphere}_{sign}_importance",
                    f"{sphere}, {label}.\n\nПочему эта ситуация важна для тебя?",
                    f"Классический Update. {sphere}",
                ),
                Question(
                    f"classic_{sphere}_{sign}_feelings",
                    f"{sphere}, {label}.\n\nКакие чувства ты испытываешь? Укажи минимум три чувства.",
                    f"Классический Update. {sphere}",
                ),
            ]
        )

CLASSIC_UPDATE_QUESTIONS.extend(
    [
        Question(
            "classic_presentation_topic",
            "Если бы ты презентовал сегодня, что хотел бы обсудить с форумом?\n\n"
            "Можно выбрать:\n"
            "- текущую ситуацию;\n"
            "- возможность;\n"
            "- тему в бизнесе, семье или личной жизни.",
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

UPDATE_QUESTIONS.extend(
    [
        Question(
            "meeting_focus",
            "Что хочешь фиксировать на встрече, пока слушаешь других?\n\n"
            "Отметь:\n"
            "- ключевые инсайты и мысли;\n"
            "- вопросы, на которые теперь будешь искать ответ;\n"
            "- что хочешь вынести из запросов других участников.",
            "Часть 4. Личный план действий",
        ),
    ]
)

POST_FORUM_PLAN_QUESTIONS = [
    Question(
        "meeting_gratitude",
        "Благодарность себе и другим: что важно не забыть проговорить?",
        "Личный план действий",
    ),
    Question(
        "next_actions",
        "Опиши действия в ближайшее время.\n\n"
        "Для каждого действия укажи:\n"
        "- глагол совершенного вида;\n"
        "- где и когда;\n"
        "- с помощью чего;\n"
        "- какой результат;\n"
        "- срок.",
        "Личный план действий",
    ),
]

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


def format_transcript_text(transcript: str, sentence_group: int = 2) -> str:
    text = re.sub(r"\s+", " ", str(transcript or "")).strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?…])\s+", text)
    sentences = [sentence.strip() for sentence in sentences if sentence.strip()]
    if len(sentences) <= 1:
        chunks = [text[i : i + 420].strip() for i in range(0, len(text), 420)]
    else:
        chunks = [
            " ".join(sentences[i : i + sentence_group]).strip()
            for i in range(0, len(sentences), sentence_group)
        ]
    return "\n\n".join(chunk for chunk in chunks if chunk)


def transcript_message(transcript: str) -> str:
    formatted = format_transcript_text(transcript)
    return f"<b>Транскрипт</b>\n\n<blockquote>{esc(formatted)}</blockquote>"


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


def time_minus_minutes(value: time, minutes: int) -> time:
    anchor = datetime.combine(date(2000, 1, 1), value)
    shifted = anchor - timedelta(minutes=minutes)
    return shifted.timetz()


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


def format_forum_date(value: Any) -> str:
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return date.fromisoformat(text).strftime("%d.%m.%Y")
    except ValueError:
        parsed = parse_forum_date(text)
        return parsed.strftime("%d.%m.%Y") if parsed else text


def normalize_username(value: str) -> str:
    return value.strip().removeprefix("@").casefold()


def normalize_report_recipient(value: str) -> str:
    username = normalize_username(value)
    if not re.fullmatch(r"[a-z0-9_]{5,32}", username):
        return ""
    return f"@{username}"


def is_profile_complete(user: dict[str, Any]) -> bool:
    return bool(user.get("next_forum_date")) and user.get("keep_files") is not None


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


def methodology_from_callback(value: str) -> str | None:
    return METHODOLOGY_CALLBACKS.get(value) or normalize_methodology(value)


def update_questions_for_user(user: dict[str, Any]) -> list[Question]:
    if methodology_for_user(user) == METHODOLOGY_CLASSIC:
        return CLASSIC_UPDATE_QUESTIONS
    return UPDATE_QUESTIONS


def load_forum_guide_context(methodology: str | None = None, max_chars: int = 18000) -> str:
    parts: list[str] = []
    normalized = normalize_methodology(methodology)
    paths = [COMMON_GUIDE_PATH]
    if normalized == METHODOLOGY_CLASSIC:
        paths.append(CLASSIC_GUIDE_PATH)
    elif normalized == METHODOLOGY_STRATEGY:
        paths.append(X_COMPETENCE_GUIDE_PATH)
    else:
        paths.extend([CLASSIC_GUIDE_PATH, X_COMPETENCE_GUIDE_PATH])
    for path in paths:
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
                diary_reminder_time TEXT,
                last_update_answers TEXT DEFAULT '{}',
                last_update_markdown TEXT,
                last_update_filename TEXT,
                last_update_methodology TEXT,
                last_update_at TEXT,
                last_post_forum_plan_answers TEXT DEFAULT '{}',
                last_post_forum_plan_at TEXT,
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
        self._ensure_column("users", "diary_reminder_time", "TEXT")
        self._ensure_column("users", "last_update_answers", "TEXT DEFAULT '{}'")
        self._ensure_column("users", "last_update_markdown", "TEXT")
        self._ensure_column("users", "last_update_filename", "TEXT")
        self._ensure_column("users", "last_update_methodology", "TEXT")
        self._ensure_column("users", "last_update_at", "TEXT")
        self._ensure_column("users", "last_post_forum_plan_answers", "TEXT DEFAULT '{}'")
        self._ensure_column("users", "last_post_forum_plan_at", "TEXT")
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
            [InlineKeyboardButton("Апдейты", callback_data="updates:menu")],
            [
                InlineKeyboardButton("Моя форум-группа", callback_data="forum_group:menu"),
                InlineKeyboardButton("Дневник", callback_data="diary:menu"),
            ],
            [
                InlineKeyboardButton("Личный кабинет", callback_data="profile:show"),
                InlineKeyboardButton("Доп. информация", callback_data="menu:info"),
            ],
        ]
    )


def onboarding_finished_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Апдейты", callback_data="updates:menu")]])


def info_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("О боте", callback_data="menu:about")],
            [
                InlineKeyboardButton("Ищу психолога", url=PSYCHOLOGIST_URL),
                InlineKeyboardButton("Ищу ментора", url=COACH_URL),
            ],
            [InlineKeyboardButton("Назад в меню", callback_data="menu:root")],
        ]
    )


def updates_menu_keyboard(user: dict[str, Any]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Новый апдейт", callback_data="update:new")],
            [InlineKeyboardButton("Мои апдейты", callback_data="updates:list")],
            [InlineKeyboardButton("Назад", callback_data="menu:root")],
        ]
    )


def forum_group_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Дата следующего форума", callback_data="menu:date")],
            [InlineKeyboardButton("Здоровье форум-группы", callback_data="menu:health")],
            [InlineKeyboardButton("Справочник форума", callback_data="guide:open")],
            [InlineKeyboardButton("О моей форум-группе", callback_data="forum_group:info")],
            [InlineKeyboardButton("Назад", callback_data="menu:root")],
        ]
    )


def diary_menu_keyboard(user: dict[str, Any]) -> InlineKeyboardMarkup:
    enabled = bool(user.get("diary_enabled"))
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Новая запись в дневнике", callback_data="diary:new")],
            [InlineKeyboardButton("Настройки дневника", callback_data="diary:mode")],
            [InlineKeyboardButton("Prompt дневника", callback_data="diary:prompt")],
            [InlineKeyboardButton("Напоминание дневника", callback_data="diary:reminder")],
            [
                InlineKeyboardButton(
                    "Выключить дневник" if enabled else "Включить дневник",
                    callback_data="diary:disable" if enabled else "diary:enable",
                )
            ],
            [InlineKeyboardButton("Назад", callback_data="menu:root")],
        ]
    )


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="menu:root")]])


def back_to_updates_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="updates:list")]])


def back_to_diary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="diary:menu")]])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Назад", callback_data="menu:root")],
        ]
    )


def onboarding_current_value(user: dict[str, Any], field: str) -> str:
    if field == "methodology":
        return methodology_for_user(user)
    if field == "keep_files":
        if user.get("keep_files") is None:
            return ""
        return "Сохранять" if user.get("keep_files") else "Удалять"
    if field == "next_forum_date":
        return format_forum_date(user.get("next_forum_date"))
    mapping = {
        "business_club": "business_club",
        "full_name": "full_name",
        "forum_group": "forum_group",
        "community_chat": "community_chat",
    }
    return str(user.get(mapping.get(field, field)) or "").strip()


def skip_keyboard(field: str, user: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    current = onboarding_current_value(user or {}, field)
    if current:
        buttons.append([InlineKeyboardButton(current, callback_data=f"onboard:{field}:keep")])
    skip_label = "Никому не отправлять" if field == "community_chat" else "Пропустить"
    buttons.append([InlineKeyboardButton(skip_label, callback_data=f"skip:{field}")])
    return InlineKeyboardMarkup(buttons)


def business_club_keyboard(prefix: str = "club", user: dict[str, Any] | None = None) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(club, callback_data=f"{prefix}:{club}")] for club in BUSINESS_CLUBS]
    current = onboarding_current_value(user or {}, "business_club")
    if prefix == "club" and current and current not in BUSINESS_CLUBS:
        buttons.insert(0, [InlineKeyboardButton(current, callback_data="onboard:business_club:keep")])
    if prefix == "club":
        buttons.append([InlineKeyboardButton("Пропустить", callback_data="skip:business_club")])
    else:
        buttons.append([InlineKeyboardButton("Назад", callback_data="profile:show")])
    return InlineKeyboardMarkup(buttons)


def methodology_keyboard(prefix: str = "methodology") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"{prefix}:{key}")]
        for key, label in METHODOLOGY_CALLBACKS.items()
    ]
    if prefix != "methodology":
        buttons.append([InlineKeyboardButton("Назад", callback_data="profile:show")])
    return InlineKeyboardMarkup(buttons)


def ai_agent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(AI_AGENT_MD_CHOICE, callback_data="ai_agent:md")],
            [InlineKeyboardButton(AI_AGENT_BOT_CHOICE, callback_data="ai_agent:bot")],
        ]
    )


def diary_reminder_keyboard(prefix: str = "diary_reminder") -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("Не включать дневник", callback_data=f"{prefix}:off")],
        [InlineKeyboardButton("Включить: 21:00 этого дня", callback_data=f"{prefix}:21")],
        [InlineKeyboardButton("Включить: 08:00 следующего дня", callback_data=f"{prefix}:08")],
    ]
    if prefix != "diary_reminder":
        buttons.append([InlineKeyboardButton("Назад", callback_data="diary:mode")])
    return InlineKeyboardMarkup(buttons)


def keep_files_keyboard(user: dict[str, Any]) -> InlineKeyboardMarkup:
    keep_is_current = user.get("keep_files") == 1
    delete_is_current = user.get("keep_files") == 0
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "Сохранять (рекомендуем, текущее)" if keep_is_current else "Сохранять (рекомендуем)",
                    callback_data="keep:1",
                ),
                InlineKeyboardButton(
                    "Удалять (текущее)" if delete_is_current else "Удалять",
                    callback_data="keep:0",
                ),
            ]
        ]
    )


def guide_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Задать вопрос по справочнику", callback_data="guide:ask")],
            [InlineKeyboardButton("Назад", callback_data="menu:root")],
        ]
    )


def update_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Новый апдейт", callback_data="update:new")],
            [InlineKeyboardButton("Изменить предыдущий", callback_data="update:edit")],
        ]
    )


def flow_keyboard(_show_next: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("⬅️", callback_data="flow:back"),
                InlineKeyboardButton("➡️", callback_data="flow:next"),
            ]
        ]
    )


MENU_TEXT_ACTIONS = {
    "апдейты": "updates.menu",
    "подготовить апдейт": "updates.start",
    "начать новый апдейт": "updates.new",
    "новый апдейт": "updates.new",
    "редактировать апдейт": "updates.edit",
    "изменить предыдущий": "updates.edit",
    "список моих апдейтов": "updates.list",
    "мои апдейты": "updates.list",
    "скачать апдейт": "updates.download",
    "скачать .md для ии": "updates.download",
    "читать апдейт": "updates.read",
    "открыть апдейт для чтения": "updates.read",
    "открыть для чтения (html)": "updates.read",
    "пообщаться по динамике апдейтов": "updates.chat",
    "моя форум-группа": "forum_group.menu",
    "дата следующего форума": "forum_group.date",
    "здоровье форум-группы": "forum_group.health",
    "справочник форума": "forum_group.guide",
    "дневник": "diary.menu",
    "новая запись в дневнике": "diary.entry",
    "режим дневника": "diary.settings",
    "промпт дневника": "diary.prompt",
    "личный кабинет": "profile.show",
    "доп. информация": "info.menu",
    "доп информация": "info.menu",
    "информация": "info.menu",
    "о боте": "info.about",
    "сделать собственный бот": "info.build_bot",
    "ищу психолога": "info.psychologist",
    "ищу коуча": "info.coach",
    "ищу ментора": "info.coach",
    "ищу ментора/коуча": "info.coach",
    "связаться с автором": "info.author",
    "удалить мои данные": "profile.delete",
}


MENU_CALLBACK_ACTIONS = {
    "menu:root": "menu.root",
    "updates:menu": "updates.menu",
    "updates:list": "updates.list",
    "updates:chat": "updates.chat",
    "updates:read": "updates.read",
    "menu:update": "updates.start",
    "update:new": "updates.new",
    "update:edit": "updates.edit",
    "profile:download_files": "updates.download",
    "forum_group:menu": "forum_group.menu",
    "forum_group:info": "forum_group.menu",
    "menu:date": "forum_group.date",
    "menu:health": "forum_group.health",
    "guide:open": "forum_group.guide",
    "guide:ask": "forum_group.guide_ask",
    "diary:menu": "diary.menu",
    "diary:new": "diary.entry",
    "diary:mode": "diary.settings",
    "diary:prompt": "diary.prompt",
    "diary:enable": "diary.prompt",
    "diary:disable": "diary.disable",
    "diary:reminder": "diary.reminder",
    "profile:show": "profile.show",
    "menu:info": "info.menu",
    "menu:about": "info.about",
    "menu:author": "info.author",
    "delete:ask": "profile.delete",
}


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


async def send_chat_action(update: Update, action: str) -> None:
    if update.effective_chat is None:
        return
    try:
        await update.effective_chat.send_action(action)
    except TelegramError as exc:
        log.debug("send_chat_action failed chat_id=%s action=%s error=%s", update.effective_chat.id, action, exc)


async def keep_chat_action(update: Update, action: str, interval_seconds: float = 4.0) -> None:
    try:
        while True:
            await send_chat_action(update, action)
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        return


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
    if context.args:
        payload = context.args[0].casefold()
        if payload == "about":
            await send_about(update)
            return
        if await handle_start_payload(update, context, user, payload):
            return
    await reply(update, "Обновил меню. Сейчас пройдём блок настроек.", reply_markup=MAIN_KEYBOARD)
    await start_onboarding(update, context, user)


async def handle_start_payload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    payload: str,
) -> bool:
    match = re.fullmatch(r"upd_(md|html|edit|plan)_([a-z0-9_-]+)", payload)
    if not match:
        return False
    action, selector = match.groups()
    if action == "md":
        await send_saved_update_files(update, user, source_selector=selector)
        return True
    if action == "html":
        await send_readable_update_file(update, user, source_selector=selector)
        return True
    if action == "plan":
        await begin_post_forum_plan_flow(update, user, source_selector=selector)
        return True
    if not await require_profile_settings(update, context, user):
        return True
    await begin_update_flow(update, user, edit_previous=True, source_selector=selector)
    return True


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
        "Бот совместим с ИИ-агентами: материалы и апдейты можно выгружать "
        "в MD-файлах. Можно отвечать текстом или голосом. Голос я транскрибирую "
        f"и покажу текст.\n\n<a href=\"{ABOUT_DEEPLINK}\">Подробно о боте</a>"
    )
    await reply(update, text, reply_markup=MAIN_KEYBOARD)
    store.update_user(
        user["telegram_user_id"],
        state="onboarding:methodology",
        active_flow=None,
        active_step=0,
        flow_payload="{}",
    )
    await reply(
        update,
        f"<b>Шаг 1/{ONBOARDING_TOTAL_STEPS}</b>\nВыбери формат форума.",
        reply_markup=methodology_keyboard(),
    )


async def show_menu(update: Update) -> None:
    await reply(
        update,
        "<b>Меню</b>\nГлавная работа здесь — апдейты. Остальное собрано в подменю.",
        reply_markup=MAIN_KEYBOARD,
    )
    await reply(update, "Выбери раздел:", reply_markup=main_inline_keyboard())


def reset_navigation_context(user: dict[str, Any]) -> dict[str, Any]:
    return store.set_flow(user["telegram_user_id"], None)


async def require_profile_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, user: dict[str, Any]) -> bool:
    if is_profile_complete(user):
        return True
    await reply(
        update,
        "Перед апдейтом нужно заполнить блок настроек: формат форума, имя, форум-группу, хранение файлов и дату форума.",
        reply_markup=MAIN_KEYBOARD,
    )
    await start_onboarding(update, context, user)
    return False


async def run_menu_action(
    action: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    *,
    interrupt: bool = True,
) -> None:
    fresh = reset_navigation_context(user) if interrupt else user
    if action == "menu.root":
        await show_menu(update)
        return
    if action == "updates.menu":
        await show_updates_menu(update, fresh)
        return
    if action == "updates.start":
        if not await require_profile_settings(update, context, fresh):
            return
        await start_update_flow(update, fresh)
        return
    if action == "updates.new":
        if not await require_profile_settings(update, context, fresh):
            return
        await begin_update_flow(update, fresh, edit_previous=False)
        return
    if action == "updates.edit":
        if not await require_profile_settings(update, context, fresh):
            return
        await begin_update_flow(update, fresh, edit_previous=True)
        return
    if action == "updates.list":
        await show_updates_list(update, fresh)
        return
    if action == "updates.download":
        await send_saved_update_files(update, fresh)
        return
    if action == "updates.read":
        await send_readable_update_file(update, fresh)
        return
    if action == "updates.chat":
        await start_updates_chat(update, fresh)
        return
    if action == "forum_group.menu":
        await show_forum_group_menu(update, fresh)
        return
    if action == "forum_group.date":
        await ask_next_forum_date(update, fresh)
        return
    if action == "forum_group.health":
        await start_health_flow(update, fresh)
        return
    if action == "forum_group.guide":
        await show_forum_guide(update, fresh)
        return
    if action == "forum_group.guide_ask":
        await start_guide_question(update, fresh)
        return
    if action == "diary.menu":
        await show_diary_menu(update, fresh)
        return
    if action == "diary.entry":
        await start_diary_entry(update, fresh)
        return
    if action == "diary.settings":
        await show_diary_mode_menu(update, fresh)
        return
    if action == "diary.prompt":
        await start_diary_prompt_setup(update, fresh, enable=True)
        return
    if action == "diary.disable":
        updated = store.update_user(fresh["telegram_user_id"], diary_enabled=0, diary_reminder_time=None, state=None)
        await reply(update, "Режим дневника выключен.", reply_markup=diary_menu_keyboard(updated))
        return
    if action == "diary.reminder":
        await reply(update, "Когда напоминать о дневнике?", reply_markup=diary_reminder_keyboard(prefix="diary_reminder"))
        return
    if action == "profile.show":
        await show_profile_cabinet(update, fresh)
        return
    if action == "profile.delete":
        await ask_delete_data(update)
        return
    if action == "info.menu":
        await show_info_menu(update)
        return
    if action == "info.about":
        await send_about(update)
        return
    if action == "info.author":
        await reply(update, esc(AUTHOR_TEXT))
        return
    if action == "info.build_bot":
        await reply(
            update,
            f'<a href="{BUILD_BOT_DOC_URL}">Инструкция: сделать собственный бот</a>\n'
            f'<a href="{REPO_URL}">Репозиторий</a>',
        )
        return
    if action == "info.psychologist":
        await reply(update, f'<a href="{PSYCHOLOGIST_URL}">Рекомендованные психологи</a>')
        return
    if action == "info.coach":
        await reply(update, f'<a href="{COACH_URL}">Менторы и коучи 5 Prism</a>')
        return
    await show_menu(update)


async def show_info_menu(update: Update) -> None:
    await reply(
        update,
        "<b>Доп. информация</b>\nВыбери, что нужно:",
        reply_markup=info_inline_keyboard(),
    )


async def show_updates_menu(update: Update, user: dict[str, Any]) -> None:
    fresh = store.update_user(user["telegram_user_id"], state=None, active_flow=None)
    await reply(
        update,
        "<b>Апдейты</b>\n\n"
        "Главный раздел для подготовки и работы с уже сохранёнными апдейтами.",
        reply_markup=updates_menu_keyboard(fresh),
    )


async def show_forum_group_menu(update: Update, user: dict[str, Any]) -> None:
    fresh = store.update_user(user["telegram_user_id"], state=None)
    await reply(
        update,
        forum_group_info_text(fresh),
        reply_markup=forum_group_menu_keyboard(),
    )


async def show_diary_menu(update: Update, user: dict[str, Any]) -> None:
    fresh = store.update_user(user["telegram_user_id"], state=None)
    status = "включён" if fresh.get("diary_enabled") else "выключен"
    reminder = fresh.get("diary_reminder_time") or "не настроено"
    await reply(
        update,
        "<b>Дневник</b>\n\n"
        f"Статус: <b>{esc(status)}</b>\n"
        f"Напоминание: <b>{esc(reminder)}</b>\n\n"
        "Можно добавить новую запись, поменять prompt обратной связи или настроить напоминание.",
        reply_markup=diary_menu_keyboard(fresh),
    )


async def cmd_menu(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "menu")
    await run_menu_action("menu.root", update, _context, user)


async def cmd_about(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "about")
    await send_about(update)


async def send_about(update: Update) -> None:
    await reply(
        update,
        "<b>О боте</b>\n\n"
        "Я помогаю готовить форумный апдейт в форматах «Классическая (YPO)» "
        "и «С личной стратегией (X-Competence)»: веду по вопросам, принимаю "
        "текст и голос, собираю .md для ИИ и .html для чтения на форуме.\n\n"
        "Ещё я напоминаю о форуме, помогаю вести дневник, храню историю апдейтов, "
        "показываю динамику, отвечаю по справочнику форума и после встречи могу "
        "собрать здоровье форум-группы.\n\n"
        "<b>Данные</b>\n"
        "Профиль, даты, ответы, дневник и файлы хранятся на сервере бота. "
        "Голосовые сообщения не хранятся: после транскрибации они удаляются. "
        "Свои данные можно удалить в «Личном кабинете».\n\n"
        "<b>Автор</b>\n"
        "Андрей Путин, Telegram: @utandr. Бот развёрнут на сервере компании kt.team.\n\n"
        "<b>Собственный бот</b>\n"
        "Можно сделать свою копию, чтобы информация была доступна только вам. "
        "Для этого понадобится всегда работающий компьютер или сервер, который "
        "будет обслуживать бота.\n\n"
        f'<a href="{REPO_URL}">Репозиторий</a> · '
        f'<a href="{BUILD_BOT_DOC_URL}">Инструкция для своего бота</a>',
        reply_markup=info_inline_keyboard(),
    )


async def cmd_cancel(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "cancel")
    await run_menu_action("menu.root", update, _context, user)


async def cmd_next_forum(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "next_forum")
    await run_menu_action("forum_group.date", update, _context, user)


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
    await run_menu_action("updates.start", update, _context, user)


async def cmd_health(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "health")
    await run_menu_action("forum_group.health", update, _context, user)


async def cmd_guide(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "guide")
    await run_menu_action("forum_group.guide", update, _context, user)


async def cmd_profile(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "profile")
    await run_menu_action("profile.show", update, _context, user)


async def cmd_diary(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    user = store.ensure_user(update)
    store.log_interaction(user["telegram_user_id"], "diary")
    await run_menu_action("diary.menu", update, _context, user)


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
    elif data.startswith("onboard:"):
        await handle_onboarding_keep(update, context, data.split(":", 2)[1])
    elif data.startswith("methodology:"):
        if user.get("state") != "onboarding:methodology":
            await reply(update, "Эта кнопка уже неактуальна. Методика меняется в личном кабинете.")
            return
        methodology = methodology_from_callback(data.split(":", 1)[1])
        await handle_onboarding_text(update, context, methodology or "")
    elif data.startswith("ai_agent:"):
        if user.get("state") != "onboarding:ai_agent":
            await reply(update, "Эта кнопка уже неактуальна. Продолжаем текущий шаг.")
            return
        choice = AI_AGENT_MD_CHOICE if data.endswith(":md") else AI_AGENT_BOT_CHOICE
        await handle_onboarding_text(update, context, choice)
    elif data.startswith("diary_reminder:"):
        value = data.split(":", 1)[1]
        if user.get("state") == "onboarding:diary_reminder":
            await handle_onboarding_diary_reminder(update, context, user, value)
        else:
            await handle_diary_reminder_choice(update, user, value)
    elif data.startswith("keep:"):
        await handle_onboarding_text(update, context, "да" if data.endswith("1") else "нет")
    elif data.startswith("skip:"):
        await handle_onboarding_skip(update, context, data.split(":", 1)[1])
    elif data.startswith("updates:edit:"):
        if not await require_profile_settings(update, context, user):
            return
        await begin_update_flow(update, user, edit_previous=True, source_selector=data.rsplit(":", 1)[1])
    elif data.startswith("updates:md:"):
        await send_saved_update_files(update, user, source_selector=data.rsplit(":", 1)[1])
    elif data.startswith("updates:html:"):
        await send_readable_update_file(update, user, source_selector=data.rsplit(":", 1)[1])
    elif data.startswith("updates:plan:"):
        await begin_post_forum_plan_flow(update, user, source_selector=data.rsplit(":", 1)[1])
    elif data in MENU_CALLBACK_ACTIONS:
        await run_menu_action(MENU_CALLBACK_ACTIONS[data], update, context, user)
    elif data.startswith("profile:edit:"):
        await start_profile_edit(update, user, data.rsplit(":", 1)[1])
    elif data.startswith("profile:club:"):
        updated = store.update_user(user["telegram_user_id"], business_club=data.split(":", 2)[2], state=None)
        await reply(update, "Бизнес-клуб обновлён.")
        await show_profile_cabinet(update, updated)
    elif data.startswith("profile:methodology:"):
        methodology = methodology_from_callback(data.split(":", 2)[2]) or DEFAULT_METHODOLOGY
        updated = store.update_user(user["telegram_user_id"], methodology=methodology, state=None)
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
        updated = store.update_user(user["telegram_user_id"], diary_enabled=0, diary_reminder_time=None, state=None)
        await reply(update, "Режим дневника выключен.", reply_markup=diary_menu_keyboard(updated))
    elif data == "diary:reminder":
        await reply(
            update,
            "Когда напоминать о дневнике?",
            reply_markup=diary_reminder_keyboard(prefix="diary_reminder"),
        )
    elif data == "delete:ask":
        await ask_delete_data(update)
    elif data == "delete:confirm":
        await delete_my_data(update, user)
    elif data == "delete:cancel":
        await reply(update, "Ок, данные оставил.", reply_markup=profile_cabinet_keyboard())
    elif data == "flow:cancel":
        store.set_flow(user["telegram_user_id"], None)
        store.update_user(user["telegram_user_id"], state=None)
        await reply(update, "Сценарий остановлен.", reply_markup=MAIN_KEYBOARD)
    elif data == "flow:next":
        await handle_flow_next(update, context, user)
    elif data == "flow:back":
        await handle_flow_back(update, user)


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
    action = MENU_TEXT_ACTIONS.get(lower)
    if action:
        await run_menu_action(action, update, context, user)
        return

    if str(user.get("state") or "").startswith("onboarding:"):
        await handle_onboarding_text(update, context, text)
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

    if user.get("state") == "updates:chat":
        await answer_updates_chat(update, context, user, text)
        return

    if str(user.get("state") or "").startswith("profile:"):
        await handle_profile_edit_text(update, user, text)
        return

    if user.get("state") == "diary:entry":
        fresh = store.update_user(user["telegram_user_id"], state=None)
        await handle_diary_entry(update, context, fresh, text)
        return

    if user.get("active_flow") == "update":
        await handle_question_answer(update, context, user, text, update_questions_for_user(user))
        return

    if user.get("active_flow") == "health":
        await handle_question_answer(update, context, user, text, HEALTH_QUESTIONS)
        return

    if user.get("active_flow") == "post_forum_plan":
        await handle_question_answer(update, context, user, text, POST_FORUM_PLAN_QUESTIONS)
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
    state = user.get("state") or "onboarding:methodology"

    if state == "onboarding:methodology":
        methodology = normalize_methodology(text)
        if methodology is None:
            await reply(
                update,
                "Формат форума нужно выбрать, этот шаг нельзя пропустить.\n\n"
                "Выбери один из двух вариантов:",
                reply_markup=methodology_keyboard(),
            )
            return
        user = store.update_user(user["telegram_user_id"], methodology=methodology, state="onboarding:ai_agent")
        await reply(
            update,
            f"<b>Шаг 2/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Как хочешь работать с Telegram-ботом?\n\n"
            f"• <b>{AI_AGENT_MD_CHOICE}</b> — я отправлю единый Markdown-файл со стандартом форума, "
            "методологией и инструкцией. Дальше ты переходишь в свой ChatGPT и анализируешь диалог там. "
            "Плюс: дальнейшая личная информация не проходит через этого бота.\n"
            f"• <b>{AI_AGENT_BOT_CHOICE}</b> — заполняем всё прямо здесь. Плюсы: бот напомнит о здоровье "
            "форум-группы, проведёт опросы, примет голосовые ответы и сможет вести дневник.",
            reply_markup=ai_agent_keyboard(),
        )
        return

    if state == "onboarding:ai_agent":
        folded = text.casefold()
        wants_md = "сложно" in folded or "md" in folded or "файл" in folded or "chatgpt" in folded
        continues_in_bot = "просто" in folded or "бот" in folded or "продолж" in folded
        if not wants_md and not continues_in_bot:
            await reply(
                update,
                "Выбери один из вариантов: сложный режим с MD-файлом или простой режим внутри Telegram-бота.",
                reply_markup=ai_agent_keyboard(),
            )
            return
        if wants_md:
            await send_ai_forum_standard_file(update, user)
        user = store.update_user(user["telegram_user_id"], state="onboarding:full_name")
        await reply(
            update,
            f"<b>Шаг 3/{ONBOARDING_TOTAL_STEPS}</b>\nНапиши Фамилию Имя.",
            reply_markup=skip_keyboard("full_name", user),
        )
        return

    if state == "onboarding:full_name":
        user = store.update_user(user["telegram_user_id"], full_name=text[:160], state="onboarding:business_club")
        await reply(
            update,
            f"<b>Шаг 4/{ONBOARDING_TOTAL_STEPS}</b>\nВыбери бизнес-клуб.",
            reply_markup=business_club_keyboard(user=user),
        )
        return

    if state == "onboarding:business_club":
        club = text.strip()
        if club not in BUSINESS_CLUBS:
            club = club[:80]
        user = store.update_user(user["telegram_user_id"], business_club=club, state="onboarding:forum_group")
        await reply(
            update,
            f"<b>Шаг 5/{ONBOARDING_TOTAL_STEPS}</b>\nКак называется твоя форум-группа?",
            reply_markup=skip_keyboard("forum_group", user),
        )
        return

    if state == "onboarding:forum_group":
        store.update_user(user["telegram_user_id"], forum_group=text[:160], state="onboarding:community_chat")
        await reply(
            update,
            f"<b>Шаг 6/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Кому отправлять статистику о здоровье форум-группы?\n\n"
            "Обычно это контакт вашего комьюнити-менеджера, если он есть. "
            "Пришли Telegram username пользователя, например <code>@utandr</code>. "
            "Бот сможет отправить ему отчёт, только если этот пользователь уже запускал бота.",
            reply_markup=skip_keyboard("community_chat", user),
        )
        return

    if state == "onboarding:community_chat":
        recipient = normalize_report_recipient(text)
        if not recipient:
            await reply(
                update,
                "Пришли Telegram username в формате <code>@username</code> или нажми «Никому не отправлять».",
                reply_markup=skip_keyboard("community_chat"),
            )
            return
        user = store.update_user(user["telegram_user_id"], community_chat=recipient, state="onboarding:keep_files")
        await reply(
            update,
            f"<b>Шаг 7/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Сохранять файлы апдейтов и загруженные документы на сервере или удалять после обработки?\n"
            "Сохранять апдейты полезно, чтобы потом видеть личную динамику и выгружать .md для ИИ.\n\n"
            "Голосовые и audio я не сохраняю никогда — удаляю сразу после транскрибации.",
            reply_markup=keep_files_keyboard(user),
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
            f"<b>Шаг 8/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Когда следующий форум? Напиши дату: например, <code>23.06.2026</code> "
            "<code>2026-06-23</code> или коротко <code>2.06</code>.",
            reply_markup=skip_keyboard("next_forum_date", user),
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
            state="onboarding:diary_reminder",
        )
        await reply(
            update,
            f"<b>Шаг 9/{ONBOARDING_TOTAL_STEPS}</b>\n"
            f"Следующий форум: <b>{forum_date.strftime('%d.%m.%Y')}</b>.\n\n"
            "Включить режим дневника? Мы будем напоминать о необходимости записать дневник "
            "вечером того же дня или утром следующего. Когда удобно?\n\n"
            "Если включить, я буду принимать текст или аудио. Свободные сообщения вне сценариев "
            "буду читать как дневник.",
            reply_markup=diary_reminder_keyboard(),
        )
        return

    if state == "onboarding:diary_reminder":
        folded = text.casefold()
        if "21" in folded or "вечер" in folded:
            await handle_onboarding_diary_reminder(update, context, user, "21")
            return
        if "8" in folded or "утр" in folded:
            await handle_onboarding_diary_reminder(update, context, user, "08")
            return
        if "не" in folded or "выкл" in folded or "нет" in folded:
            await handle_onboarding_diary_reminder(update, context, user, "off")
            return
        await reply(
            update,
            "Выбери, включать ли дневник и когда напоминать.",
            reply_markup=diary_reminder_keyboard(),
        )
        return


async def handle_onboarding_keep(
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
    value = onboarding_current_value(user, field)
    if not value:
        await reply(update, "Сохранённого значения нет. Введи новое значение или нажми «Пропустить».")
        return
    if field == "keep_files":
        value = "да" if user.get("keep_files") else "нет"
    await handle_onboarding_text(update, context, value)


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
            "Формат форума нужно выбрать, этот шаг нельзя пропустить.",
            reply_markup=methodology_keyboard(),
        )
        return

    if field == "business_club":
        user = store.update_user(user["telegram_user_id"], business_club="", state="onboarding:forum_group")
        await reply(
            update,
            "Ок, очистил бизнес-клуб.\n\n"
            f"<b>Шаг 5/{ONBOARDING_TOTAL_STEPS}</b>\nКак называется твоя форум-группа?",
            reply_markup=skip_keyboard("forum_group", user),
        )
        return

    if field == "full_name":
        user = store.update_user(
            user["telegram_user_id"],
            full_name="",
            state="onboarding:business_club",
        )
        await reply(
            update,
            "Ок, очистил Фамилию Имя.\n\n"
            f"<b>Шаг 4/{ONBOARDING_TOTAL_STEPS}</b>\nВыбери бизнес-клуб.",
            reply_markup=business_club_keyboard(user=user),
        )
        return

    if field == "forum_group":
        user = store.update_user(user["telegram_user_id"], forum_group="", state="onboarding:community_chat")
        await reply(
            update,
            "Ок, очистил форум-группу.\n\n"
            f"<b>Шаг 6/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Кому отправлять статистику о здоровье форум-группы?\n\n"
            "Обычно это контакт вашего комьюнити-менеджера, если он есть. "
            "Пришли Telegram username пользователя, например <code>@utandr</code>. "
            "Бот сможет отправить ему отчёт, только если этот пользователь уже запускал бота.",
            reply_markup=skip_keyboard("community_chat", user),
        )
        return

    if field == "community_chat":
        user = store.update_user(user["telegram_user_id"], community_chat="", state="onboarding:keep_files")
        await reply(
            update,
            "Ок, отчёты о здоровье пока будут оставаться в личном чате.\n\n"
            f"<b>Шаг 7/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Сохранять файлы апдейтов и загруженные документы на сервере или удалять после обработки?\n"
            "Сохранять апдейты полезно, чтобы потом видеть личную динамику и выгружать .md для ИИ.\n\n"
            "Голосовые и audio я не сохраняю никогда — удаляю сразу после транскрибации.",
            reply_markup=keep_files_keyboard(user),
        )
        return

    if field == "keep_files":
        user = store.update_user(user["telegram_user_id"], keep_files=0, state="onboarding:next_forum_date")
        await reply(
            update,
            "Ок, по умолчанию буду удалять файлы после обработки.\n\n"
            f"<b>Шаг 8/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Когда следующий форум? Можно написать <code>23.06.2026</code> или коротко <code>2.06</code>.",
            reply_markup=skip_keyboard("next_forum_date", user),
        )
        return

    if field == "next_forum_date":
        forum_date = default_forum_date()
        user = store.update_user(
            user["telegram_user_id"],
            next_forum_date=forum_date.isoformat(),
            state="onboarding:diary_reminder",
        )
        await reply(
            update,
            f"Ок, поставил временную дату форума: <b>{forum_date.strftime('%d.%m.%Y')}</b>. "
            "Её можно поменять в личном кабинете.\n\n"
            f"<b>Шаг 9/{ONBOARDING_TOTAL_STEPS}</b>\n"
            "Включить режим дневника? Мы будем напоминать о необходимости записать дневник "
            "вечером того же дня или утром следующего. Когда удобно?\n\n"
            "Если включить, я буду принимать текст или аудио.",
            reply_markup=diary_reminder_keyboard(),
        )
        return


async def handle_onboarding_diary_reminder(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    value: str,
) -> None:
    updated = apply_diary_reminder_choice(user, value, finish_onboarding=True)
    await reply(update, onboarding_finished_text(updated), reply_markup=onboarding_finished_keyboard())
    await notify_admin_new_user(context, updated)


async def handle_diary_reminder_choice(update: Update, user: dict[str, Any], value: str) -> None:
    updated = apply_diary_reminder_choice(user, value)
    if updated.get("diary_enabled"):
        await reply(
            update,
            f"Напоминание дневника настроено: <b>{esc(updated.get('diary_reminder_time') or '')}</b>.",
            reply_markup=diary_menu_keyboard(updated),
        )
    else:
        await reply(update, "Напоминания дневника выключены.", reply_markup=diary_menu_keyboard(updated))


def apply_diary_reminder_choice(
    user: dict[str, Any],
    value: str,
    finish_onboarding: bool = False,
) -> dict[str, Any]:
    fields: dict[str, Any] = {"state": None}
    if value == "off":
        fields.update(diary_enabled=0, diary_reminder_time=None)
    else:
        reminder_time = DIARY_REMINDER_CHOICES.get(value, DIARY_REMINDER_CHOICES["21"])[0]
        fields.update(
            diary_enabled=1,
            diary_feedback_prompt=(user.get("diary_feedback_prompt") or DEFAULT_DIARY_PROMPT),
            diary_reminder_time=reminder_time,
        )
    if not finish_onboarding:
        fields["state"] = None
    return store.update_user(user["telegram_user_id"], **fields)


def onboarding_finished_text(user: dict[str, Any]) -> str:
    forum_date = format_forum_date(user.get("next_forum_date")) or "не указана"
    if user.get("diary_enabled"):
        diary = f"Дневник включён, напоминание {user.get('diary_reminder_time') or 'без времени'}."
    else:
        diary = "Дневник выключен."
    return f"Профиль готов. Следующий форум: <b>{esc(forum_date)}</b>.\n{esc(diary)}"


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
    forum_date = format_forum_date(user.get("next_forum_date")) or "не указана"
    diary_reminder = user.get("diary_reminder_time") or "не настроено"
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
        f"Напоминание дневника: <b>{esc(diary_reminder)}</b>\n\n"
        "Все поля можно изменить здесь."
    )


def forum_group_info_text(user: dict[str, Any]) -> str:
    report_recipient = (user.get("community_chat") or "").strip()
    forum_date = format_forum_date(user.get("next_forum_date")) or "не указана"
    recipient_text = report_recipient or "никому не отправлять"
    return (
        "<b>Моя форум-группа</b>\n\n"
        f"Название: <b>{esc(user.get('forum_group') or 'не указано')}</b>\n"
        f"Методика: <b>{esc(methodology_for_user(user))}</b>\n"
        f"Следующий форум: <b>{esc(forum_date)}</b>\n"
        f"Получатель health check: <b>{esc(recipient_text)}</b>\n\n"
        "Здесь можно запустить health check, изменить дату форума или открыть справочник."
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
            [InlineKeyboardButton("Удалить мои данные", callback_data="delete:ask")],
            [InlineKeyboardButton("Назад", callback_data="menu:root")],
        ]
    )


async def show_profile_cabinet(update: Update, user: dict[str, Any]) -> None:
    fresh = store.get_user(user["telegram_user_id"]) or user
    store.update_user(user["telegram_user_id"], state=None)
    await reply(update, profile_cabinet_text(fresh), reply_markup=profile_cabinet_keyboard())


async def start_profile_edit(update: Update, user: dict[str, Any], field: str) -> None:
    if field == "business_club":
        store.update_user(user["telegram_user_id"], state=None, active_flow=None)
        await reply(update, "Выбери бизнес-клуб.", reply_markup=business_club_keyboard(prefix="profile:club"))
        return
    if field == "methodology":
        store.update_user(user["telegram_user_id"], state=None, active_flow=None)
        await reply(update, "Выбери методику подготовки апдейта.", reply_markup=methodology_keyboard(prefix="profile:methodology"))
        return
    if field == "keep_files":
        store.update_user(user["telegram_user_id"], state=None, active_flow=None)
        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("Сохранять", callback_data="profile:keep:1"),
                    InlineKeyboardButton("Удалять", callback_data="profile:keep:0"),
                ],
                [InlineKeyboardButton("Назад", callback_data="profile:show")],
            ]
        )
        await reply(update, "Как поступать с файлами после обработки?", reply_markup=keyboard)
        return
    if field == "community_chat":
        store.update_user(user["telegram_user_id"], state="profile:community_chat", active_flow=None)
        keyboard = InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Очистить", callback_data="profile:clear_community")],
                [InlineKeyboardButton("Назад", callback_data="profile:show")],
            ]
        )
        await reply(
            update,
            "Пришли Telegram username пользователя, например <code>@utandr</code>. "
            "Бот отправит ему отчёт, только если этот пользователь уже запускал бота. "
            "Если очистить поле, отчёты останутся в личном чате.",
            reply_markup=keyboard,
        )
        return
    if field == "next_forum_date":
        store.update_user(user["telegram_user_id"], state="profile:next_forum_date", active_flow=None)
        await reply(
            update,
            "Пришли новую дату форума: например, <code>23.06.2026</code>.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="profile:show")]]),
        )
        return
    prompts = {
        "full_name": "Напиши новые Фамилию Имя.",
        "forum_group": "Напиши новое название форум-группы.",
    }
    if field not in prompts:
        await reply(update, "Не понял, какое поле изменить.")
        return
    store.update_user(user["telegram_user_id"], state=f"profile:{field}", active_flow=None)
    await reply(
        update,
        prompts[field],
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="profile:show")]]),
    )


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


def saved_update_files(user: dict[str, Any]) -> list[Path]:
    user_dir = UPDATES_DIR / str(user["telegram_user_id"])
    if not user_dir.exists():
        return []
    return sorted(user_dir.glob("*.md"), key=lambda path: path.stat().st_mtime, reverse=True)


def stored_update_items(user: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, path in enumerate(saved_update_files(user)):
        items.append(
            {
                "selector": str(index),
                "filename": path.name,
                "date": datetime.fromtimestamp(path.stat().st_mtime, TZ).strftime("%d.%m.%Y"),
                "path": path,
            }
        )
    latest_markdown = str(user.get("last_update_markdown") or "").strip()
    latest_filename = str(user.get("last_update_filename") or "").strip()
    if latest_markdown and latest_filename and not any(item["filename"] == latest_filename for item in items):
        latest_at = format_forum_date(str(user.get("last_update_at") or "")[:10]) or datetime.now(TZ).strftime("%d.%m.%Y")
        items.insert(
            0,
            {
                "selector": "latest",
                "filename": latest_filename,
                "date": latest_at,
                "path": None,
            },
        )
    return items


def updates_list_keyboard(user: dict[str, Any]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Динамика апдейтов", callback_data="updates:chat")],
            [InlineKeyboardButton("Назад", callback_data="updates:menu")],
        ]
    )


def update_action_deeplink(action: str, selector: str) -> str:
    return f"https://t.me/{BOT_USERNAME}?start=upd_{action}_{selector}"


def update_item_line(index: int, item: dict[str, Any]) -> str:
    selector = str(item["selector"])
    return (
        f"<b>{esc(item['date'])}</b>\n"
        f' - Скачать: <a href="{update_action_deeplink("md", selector)}">[.md]</a> (полная, для ИИ) '
        f'<a href="{update_action_deeplink("html", selector)}">[.html]</a> (короткая, для чтения)\n'
        f' - <a href="{update_action_deeplink("edit", selector)}">Редактировать</a>\n'
        f' - <a href="{update_action_deeplink("plan", selector)}">Ввести личный план действий по разбору</a>'
    )


def looks_like_utf8_mojibake(text: str) -> bool:
    markers = sum(text.count(marker) for marker in ("Ð", "Ñ", "Â", "â", "�"))
    cyrillic = len(re.findall(r"[А-Яа-яЁё]", text))
    return markers >= 5 and markers > cyrillic


def repair_utf8_mojibake(text: str) -> str:
    if not looks_like_utf8_mojibake(text):
        return text
    best = text
    best_score = sum(best.count(marker) for marker in ("Ð", "Ñ", "Â", "â", "�"))
    for encoding in ("latin1", "cp1252"):
        try:
            candidate = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        score = sum(candidate.count(marker) for marker in ("Ð", "Ñ", "Â", "â", "�"))
        if score < best_score and re.search(r"[А-Яа-яЁё]", candidate):
            best = candidate
            best_score = score
    return best


def decode_markdown_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def markdown_bytes_for_download(markdown: str) -> bytes:
    text = repair_utf8_mojibake(str(markdown or "").lstrip("\ufeff"))
    return UTF8_BOM + text.encode("utf-8")


def ensure_markdown_file_has_utf8_bom(path: Path) -> bool:
    try:
        original = path.read_bytes()
    except OSError as exc:
        log.warning("cannot read markdown file for encoding migration path=%s error=%s", path, exc)
        return False
    text = repair_utf8_mojibake(decode_markdown_bytes(original))
    normalized = markdown_bytes_for_download(text)
    if original == normalized:
        return False
    try:
        path.write_bytes(normalized)
    except OSError as exc:
        log.warning("cannot migrate markdown file to utf-8 bom path=%s error=%s", path, exc)
        return False
    return True


def read_markdown_file_text(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    text = repair_utf8_mojibake(decode_markdown_bytes(data)).strip()
    ensure_markdown_file_has_utf8_bom(path)
    return text


def migrate_saved_update_markdown_files() -> int:
    if not UPDATES_DIR.exists():
        return 0
    migrated = 0
    for path in UPDATES_DIR.rglob("*.md"):
        if ensure_markdown_file_has_utf8_bom(path):
            migrated += 1
    return migrated


def latest_update_markdown(user: dict[str, Any], source_selector: str | None = None) -> tuple[str, str] | None:
    files = saved_update_files(user)
    if source_selector and source_selector != "latest":
        try:
            path = files[int(source_selector)]
        except (ValueError, IndexError):
            return None
        text = read_markdown_file_text(path)
        return (text, path.name) if text else None
    if source_selector == "latest":
        markdown = str(user.get("last_update_markdown") or "").strip()
        if markdown:
            filename = user.get("last_update_filename") or f"forum-update-{datetime.now(TZ).strftime('%Y%m%d-%H%M')}.md"
            return repair_utf8_mojibake(markdown), str(filename)
        return None
    if files:
        text = read_markdown_file_text(files[0])
        if text:
            return text, files[0].name
    markdown = str(user.get("last_update_markdown") or "").strip()
    if markdown:
        filename = user.get("last_update_filename") or f"forum-update-{datetime.now(TZ).strftime('%Y%m%d-%H%M')}.md"
        return repair_utf8_mojibake(markdown), str(filename)
    return None


def selected_update_path(user: dict[str, Any], source_selector: str | None) -> Path | None:
    if not source_selector or source_selector == "latest":
        return None
    files = saved_update_files(user)
    try:
        return files[int(source_selector)]
    except (ValueError, IndexError):
        return None


def write_selected_update_markdown(
    user: dict[str, Any],
    source_selector: str | None,
    markdown: str,
    filename: str,
) -> None:
    normalized = repair_utf8_mojibake(str(markdown or "").strip()) + "\n"
    path = selected_update_path(user, source_selector)
    if path is not None:
        path.write_bytes(markdown_bytes_for_download(normalized))
    if source_selector == "latest" or not path or str(user.get("last_update_filename") or "") == filename:
        store.update_user(
            user["telegram_user_id"],
            last_update_markdown=normalized,
            last_update_filename=filename,
            last_update_at=now_iso(),
        )


async def send_temp_document(
    update: Update,
    payload: bytes,
    *,
    filename: str,
    suffix: str,
    caption: str,
) -> None:
    if update.effective_message is None or update.effective_chat is None:
        return
    tmp = tempfile.NamedTemporaryFile(prefix="forum-update-", suffix=suffix, delete=False)
    tmp_path = Path(tmp.name)
    try:
        tmp.write(payload)
        tmp.close()
        with tmp_path.open("rb") as fh:
            await spaced_bot_send(
                update.effective_chat.id,
                lambda: update.effective_message.reply_document(
                    document=fh,
                    filename=filename,
                    caption=caption,
                ),
            )
    finally:
        tmp_path.unlink(missing_ok=True)


def markdown_inline_to_html(text: str) -> str:
    escaped = html.escape(text, quote=False)
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"(?<!\*)_(.+?)_(?!_)", r"<em>\1</em>", escaped)
    return escaped


def shorten_thesis(text: str, max_chars: int = 520) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip(" ,.;:") + "..."


def split_answer_theses(answer: str) -> list[str]:
    clean = str(answer or "").strip()
    if not clean or clean == "_Нет ответа_":
        return []
    theses: list[str] = []
    for block in re.split(r"\n\s*\n", clean):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        if all(re.match(r"^[-•]\s+", line) for line in lines):
            theses.extend(re.sub(r"^[-•]\s+", "", line).strip() for line in lines)
        else:
            theses.append(" ".join(lines))
    return [shorten_thesis(thesis) for thesis in theses if thesis.strip() and thesis.strip() != "_Нет ответа_"]


def rating_value(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        rating = int(value)
    except ValueError:
        return None
    if 1 <= rating <= 10:
        return str(rating)
    return None


def extract_rating_summary(answer: str) -> str | None:
    text = re.sub(r"\s+", " ", str(answer or "")).strip()
    if not text:
        return None
    direct = re.search(r"\b(10|[1-9])\s*(?:->|→)\s*(10|[1-9])\s*/?\s*10\b", text)
    if direct:
        previous = rating_value(direct.group(1))
        current = rating_value(direct.group(2))
        return f"{previous}->{current}/10" if previous and current else None

    current_match = re.search(
        r"(?:этого|текущ(?:его|ий|ая)|сейчас|нынешн(?:его|ий|ая))[^0-9]{0,80}(10|[1-9])(?:\s*/\s*10)?",
        text,
        flags=re.I,
    )
    previous_match = re.search(
        r"(?:предыдущ(?:его|ий|ая)|прошл(?:ого|ый|ая))[^0-9]{0,80}(10|[1-9])(?:\s*/\s*10)?",
        text,
        flags=re.I,
    )
    current = rating_value(current_match.group(1) if current_match else None)
    previous = rating_value(previous_match.group(1) if previous_match else None)
    if current:
        return f"{previous}->{current}/10" if previous else f"{current}/10"

    ratings = [rating_value(match) for match in re.findall(r"\b(10|[1-9])\s*/\s*10\b", text)]
    ratings = [rating for rating in ratings if rating]
    if len(ratings) >= 2:
        current, previous = ratings[0], ratings[1]
        return f"{previous}->{current}/10"
    if ratings:
        return f"{ratings[0]}/10"
    return None


def strip_rating_prefix(thesis: str) -> str:
    text = thesis.strip()
    text = re.sub(
        r"^(?:оценк[а-яё\s]*(?:месяца|периода)?\s*[:—-]\s*)?(?:10|[1-9])\s*/\s*10\s*[.;,:—-]*\s*",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.sub(
        r"^(?:оценк[а-яё\s]*(?:этого|текущего|предыдущего|прошлого)[^.;:]*[:—-]?\s*(?:10|[1-9])(?:\s*/\s*10)?[.;,:—-]*\s*)+",
        "",
        text,
        flags=re.I,
    ).strip()
    return text


def parse_update_metadata(markdown: str) -> dict[str, str]:
    metadata: dict[str, str] = {}
    header = markdown.split("\n## ", 1)[0]
    for match in re.finditer(r"^-\s*([^:]+):\s*(.+)$", header, flags=re.M):
        key = match.group(1).strip()
        value = match.group(2).strip()
        if value:
            metadata[key] = value
    return metadata


def answer_text_is_filled(answer: str) -> bool:
    clean = str(answer or "").strip()
    return bool(clean and clean != "_Нет ответа_")


def section_has_filled_answers(section_content: str) -> bool:
    for match in re.finditer(r"\*\*(.*?)\*\*\s*\n\n(.*?)(?=\n\*\*|\n## |\Z)", section_content, flags=re.S):
        if answer_text_is_filled(match.group(2)):
            return True
    return False


def strip_empty_personal_plan_sections(markdown: str) -> str:
    def replace(match: re.Match[str]) -> str:
        title = match.group(1).strip()
        content = match.group(2)
        if title.casefold() == "личный план действий" and not section_has_filled_answers(content):
            return ""
        return match.group(0)

    return re.sub(r"^##\s+(.+?)\s*$\n(.*?)(?=^##\s+|\Z)", replace, markdown, flags=re.M | re.S).strip() + "\n"


def parse_update_answer_items(markdown: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    markdown = strip_empty_personal_plan_sections(markdown)
    for section in re.finditer(r"^##\s+(.+?)\s*$\n(.*?)(?=^##\s+|\Z)", markdown, flags=re.M | re.S):
        section_title = section.group(1).strip()
        section_content = section.group(2)
        for match in re.finditer(r"\*\*(.*?)\*\*\s*\n\n(.*?)(?=\n\*\*|\n## |\Z)", section_content, flags=re.S):
            answer = match.group(2).strip()
            if answer_text_is_filled(answer):
                items.append(
                    {
                        "section": section_title,
                        "prompt": compact_markdown_key(match.group(1)),
                        "answer": answer,
                    }
                )
    return items


BRIEF_SPHERE_ORDER = ("Я", "Моё дело", "Моя семья / близкие", "Бизнес", "Семья", "Личное")


def brief_sphere_for_item(section: str, prompt: str) -> str | None:
    for sphere in BRIEF_SPHERE_ORDER:
        if prompt.startswith(f"{sphere}:") or prompt.startswith(f"{sphere},"):
            return sphere
        if section.endswith(f". {sphere}") or section == sphere:
            return sphere
    return None


def brief_question_label(prompt: str) -> str:
    first_line = prompt.splitlines()[0].strip().rstrip(".:")
    lowered = first_line.casefold()
    if "дай оценку месяца" in lowered or "поставь оценку месяца" in lowered:
        return "Оценка месяца"
    if "собери ретроспективу" in lowered:
        return "Как было / что получилось"
    if "опиши следующий период" in lowered:
        return "Чего хочу / следующий период"
    if "самое важное" in lowered:
        return "Что произошло"
    if "почему эта ситуация важна" in lowered:
        return "Почему важно / цена вопроса"
    if "какие чувства" in lowered:
        return "Чувства"
    if "сформулируй главный запрос" in lowered:
        return "Главный запрос и контекст"
    if "денежный эквивалент" in lowered:
        return "Цена вопроса"
    if "идеальный результат" in lowered:
        return "Идеальный результат и история"
    if "что уже пробовал" in lowered:
        return "Что уже пробовал"
    if "выбери рабочую гипотезу" in lowered:
        return "Рабочая гипотеза"
    if "какая помощь нужна" in lowered:
        return "Помощь от группы"
    if "что хочешь фиксировать" in lowered:
        return "Что фиксировать на встрече"
    if "5% самых радостных" in lowered:
        return "5% радостного"
    if "5% самых тяжёлых" in lowered:
        return "5% тяжёлого"
    if "если бы ты презентовал" in lowered:
        return "Тема для форума"
    if "над чем ты хотел бы поработать" in lowered:
        return "Главный вопрос к форуму"
    classic_match = re.match(r"^(Бизнес|Семья|Личное),\s*(плюс|минус)", first_line, flags=re.I)
    if classic_match:
        return classic_match.group(2).capitalize()
    return first_line


def item_answer_html(item: dict[str, str]) -> str:
    theses = split_answer_theses(item["answer"])
    raw_label = brief_question_label(item["prompt"])
    if not theses:
        return ""
    bullets: list[str] = []
    if raw_label == "Оценка месяца":
        if rating_summary := extract_rating_summary(item["answer"]):
            bullets.append(render_brief_bullet(raw_label, rating_summary))
        theses = [cleaned for thesis in theses if (cleaned := strip_rating_prefix(thesis))]
    bullets.extend(render_brief_bullet(raw_label, thesis) for thesis in theses)
    if not bullets:
        return ""
    return f'<div class="qa"><ul class="theses">{"".join(bullets)}</ul></div>'


def render_brief_bullet(label: str, text: str) -> str:
    label = str(label or "").strip()
    text = str(text or "").strip()
    if not label and not text:
        return ""
    if label and text:
        content = f"{markdown_inline_to_html(label)}: {markdown_inline_to_html(text)}"
    else:
        content = markdown_inline_to_html(label or text)
    return f"<li><strong>{content}</strong></li>"


def render_brief_html_document(body: list[str], title: str = "Форумный апдейт") -> str:
    document_title = html.escape(title, quote=True)
    return (
        "<!doctype html>\n"
        '<html lang="ru">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{document_title}</title>\n"
        "<style>\n"
        ":root{color-scheme:light dark;}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "font-size:17px;line-height:1.55;background:#f7f7f4;color:#1f2328;}main{max-width:760px;margin:0 auto;"
        "padding:28px 18px 52px;}h1{font-size:28px;line-height:1.18;margin:0 0 22px;}h2{font-size:22px;"
        "line-height:1.25;margin:34px 0 12px;padding-top:16px;border-top:1px solid #ddd8ce;}p{margin:8px 0 16px;}"
        "ul,ol{padding-left:24px;margin:8px 0 18px;}li{margin:6px 0;}"
        ".subtitle{color:#62666d;margin-top:-10px;}.meta{list-style:none;padding:12px 14px;margin:18px 0 24px;"
        "background:rgba(184,137,66,.10);border-left:4px solid #b88942;}.qa{margin:12px 0 18px;}"
        ".theses{padding-left:22px;}strong{font-weight:700;}em{color:#62666d;}"
        "@media(prefers-color-scheme:dark){body{background:#111;color:#eee;}h2{border-color:#333;}"
        ".meta{background:rgba(184,137,66,.16);}.subtitle,em{color:#b8b8b8;}}\n"
        "</style>\n"
        "</head>\n"
        f"<body><main>{''.join(body)}</main></body>\n"
        "</html>\n"
    )


def update_markdown_to_brief_html_body(markdown: str) -> tuple[str, list[str]]:
    text = repair_utf8_mojibake(str(markdown or "").lstrip("\ufeff"))
    title_match = re.search(r"^#\s+(.+)$", text, flags=re.M)
    title = title_match.group(1).strip() if title_match else "Форумный апдейт"
    metadata = parse_update_metadata(text)
    items = parse_update_answer_items(text)
    body = [
        f"<h1>{markdown_inline_to_html(title)}</h1>",
        '<p class="subtitle">Короткая версия для чтения на форуме</p>',
    ]
    meta_labels = {"Создано": "Дата заполнения"}
    meta_items = []
    for key in ("Участник", "Методика", "Дата форума", "Создано"):
        if metadata.get(key):
            label = meta_labels.get(key, key)
            meta_items.append(
                f"<li><strong>{markdown_inline_to_html(label)}:</strong> {markdown_inline_to_html(metadata[key])}</li>"
            )
    if meta_items:
        body.append(f'<ul class="meta">{"".join(meta_items)}</ul>')

    grouped_spheres: dict[str, list[dict[str, str]]] = {sphere: [] for sphere in BRIEF_SPHERE_ORDER}
    section_groups: dict[str, list[dict[str, str]]] = {}
    for item in items:
        sphere = brief_sphere_for_item(item["section"], item["prompt"])
        if sphere:
            grouped_spheres.setdefault(sphere, []).append(item)
            continue
        section_groups.setdefault(item["section"], []).append(item)

    rendered = False
    for sphere in BRIEF_SPHERE_ORDER:
        sphere_items = grouped_spheres.get(sphere) or []
        if not sphere_items:
            continue
        body.append(f"<h2>{markdown_inline_to_html(sphere)}</h2>")
        body.extend(item_html for item in sphere_items if (item_html := item_answer_html(item)))
        rendered = True
    for section, section_items in section_groups.items():
        rendered_items = [item_html for item in section_items if (item_html := item_answer_html(item))]
        if not rendered_items:
            continue
        body.append(f"<h2>{markdown_inline_to_html(section)}</h2>")
        body.extend(rendered_items)
        rendered = True
    if not rendered:
        body.append("<p>В апдейте пока нет заполненных тезисов.</p>")
    return title, body


def markdown_to_readable_html(markdown: str, title: str = "Форумный апдейт") -> str:
    _update_title, body = update_markdown_to_brief_html_body(markdown)
    return render_brief_html_document(body, title)


def extract_json_object(text: str) -> dict[str, Any]:
    clean = str(text or "").strip()
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", clean, flags=re.I | re.S).strip()
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start = clean.find("{")
        end = clean.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(clean[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("AI HTML brief response must be a JSON object")
    return value


def ai_brief_data_to_html_body(data: dict[str, Any], fallback_title: str) -> tuple[str, list[str]]:
    title = str(data.get("title") or fallback_title or "Форумный апдейт").strip()
    body = [
        f"<h1>{markdown_inline_to_html(title)}</h1>",
        '<p class="subtitle">Короткая версия для чтения на форуме</p>',
    ]

    meta_items: list[str] = []
    raw_meta = data.get("meta") or []
    if isinstance(raw_meta, dict):
        raw_meta = [{"label": key, "value": value} for key, value in raw_meta.items()]
    if isinstance(raw_meta, list):
        for item in raw_meta:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "").strip()
            value = str(item.get("value") or "").strip()
            if label and value:
                meta_items.append(
                    f"<li><strong>{markdown_inline_to_html(label)}:</strong> {markdown_inline_to_html(value)}</li>"
                )
    if meta_items:
        body.append(f'<ul class="meta">{"".join(meta_items)}</ul>')

    rendered = False
    for section in data.get("sections") or []:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("title") or "").strip()
        raw_bullets = section.get("bullets") or []
        bullets: list[str] = []
        if isinstance(raw_bullets, list):
            for bullet in raw_bullets:
                if isinstance(bullet, dict):
                    rendered_bullet = render_brief_bullet(
                        str(bullet.get("label") or "").strip(),
                        str(bullet.get("text") or "").strip(),
                    )
                else:
                    rendered_bullet = render_brief_bullet("", str(bullet or "").strip())
                if rendered_bullet:
                    bullets.append(rendered_bullet)
        if not heading or not bullets:
            continue
        body.append(f"<h2>{markdown_inline_to_html(heading)}</h2>")
        body.append(f'<div class="qa"><ul class="theses">{"".join(bullets)}</ul></div>')
        rendered = True
    if not rendered:
        raise ValueError("AI HTML brief response has no renderable sections")
    return title, body


async def markdown_to_ai_readable_html(markdown: str, title: str = "Форумный апдейт") -> str:
    if _openai is None:
        return markdown_to_readable_html(markdown, title=title)

    normalized = strip_empty_personal_plan_sections(repair_utf8_mojibake(str(markdown or "").lstrip("\ufeff")))
    fallback_title_match = re.search(r"^#\s+(.+)$", normalized, flags=re.M)
    fallback_title = fallback_title_match.group(1).strip() if fallback_title_match else title
    system = (
        "Ты редактор форумного апдейта. На входе полный Markdown-апдейт. "
        "Нужно сделать короткую версию для чтения автором на форум-встрече. "
        "Не добавляй интерпретаций, советов и новых смыслов. Бери только то, что есть в апдейте. "
        "Сохраняй структуру из Markdown: метаданные, затем основные разделы и сферы. "
        "Каждый ответ сократи до ключевых тезисов. Для оценки месяца дай отдельный тезис "
        "в формате 'предыдущая->текущая/10', например '6->8/10'; если предыдущей нет, '8/10'. "
        "Возвращай только JSON без Markdown-разметки и без HTML."
    )
    prompt = (
        "Верни JSON строго такой формы:\n"
        "{\n"
        '  "title": "Форум-апдейт ...",\n'
        '  "meta": [{"label": "Участник", "value": "..."}, {"label": "Дата форума", "value": "..."}],\n'
        '  "sections": [\n'
        '    {"title": "Я", "bullets": [{"label": "Оценка месяца", "text": "6->8/10"}, '
        '{"label": "Как было / что получилось", "text": "ключевой тезис"}]}\n'
        "  ]\n"
        "}\n\n"
        "Правила:\n"
        "- Оставь дату заполнения, если она есть в Markdown.\n"
        "- Сохрани разделение по сферам: Я, Моё дело, Моя семья / близкие или Бизнес, Семья, Личное.\n"
        "- Раздел «Личный план действий» включай только если в нём есть реальные ответы; пустой раздел или `_Нет ответа_` полностью пропускай.\n"
        "- В bullets используй короткую метку вопроса в label и один тезис в text.\n"
        "- text должен быть обычным текстом без HTML и Markdown.\n"
        "- Лучше 1-3 тезиса на вопрос, только самое важное.\n\n"
        f"Markdown-апдейт:\n{normalized[:50000]}"
    )

    def _call() -> str:
        response = _openai.responses.create(
            model=OPENAI_HTML_MODEL,
            instructions=system,
            input=prompt,
            max_output_tokens=3200,
            text={"verbosity": "low"},
        )
        return extract_response_text(response)

    try:
        ai_text = await asyncio.to_thread(_call)
        _ai_title, body = ai_brief_data_to_html_body(extract_json_object(ai_text), fallback_title)
        return render_brief_html_document(body, title)
    except Exception as exc:
        log.warning("AI readable HTML failed: %s", exc)
        return markdown_to_readable_html(markdown, title=title)


async def send_saved_update_files(update: Update, user: dict[str, Any], source_selector: str | None = None) -> None:
    latest = latest_update_markdown(user, source_selector)
    if latest is None:
        await reply(
            update,
            "Сохранённых .md апдейтов пока нет. Сначала подготовь апдейт.",
            reply_markup=updates_menu_keyboard(user),
        )
        return
    markdown, filename = latest
    await send_temp_document(
        update,
        markdown_bytes_for_download(markdown),
        filename=filename,
        suffix=".md",
        caption=UPDATE_MD_CAPTION,
    )


async def send_readable_update_file(update: Update, user: dict[str, Any], source_selector: str | None = None) -> None:
    latest = latest_update_markdown(user, source_selector)
    if latest is None:
        await reply(
            update,
            "Сохранённых апдейтов пока нет. Сначала подготовь апдейт.",
            reply_markup=updates_menu_keyboard(user),
        )
        return
    markdown, filename = latest
    await reply(
        update,
        "<b>Готовлю HTML-версию апдейта</b>\n\n"
        "Сейчас выделяю ключевые тезисы из полной .md-версии с помощью ИИ. "
        "Обычно это занимает 20–30 секунд — просто подожди, файл придёт сюда.",
    )
    progress_task = asyncio.create_task(keep_chat_action(update, ChatAction.TYPING))
    try:
        readable = await markdown_to_ai_readable_html(markdown, title=Path(filename).stem)
    finally:
        progress_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await progress_task
    html_digest = hashlib.sha1(readable.encode("utf-8")).hexdigest()[:8]
    html_filename = f"{Path(filename).stem}-read-{html_digest}.html"
    await send_chat_action(update, ChatAction.UPLOAD_DOCUMENT)
    await send_temp_document(
        update,
        readable.encode("utf-8"),
        filename=html_filename,
        suffix=".html",
        caption=UPDATE_HTML_CAPTION,
    )


def compact_markdown_key(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def methodology_from_update_markdown(markdown: str) -> str | None:
    match = re.search(r"^- Методика:\s*(.+)$", markdown, flags=re.M)
    return normalize_methodology(match.group(1)) if match else None


def parse_update_markdown_answers(markdown: str, questions: list[Question]) -> dict[str, str]:
    prompt_to_answer: dict[str, str] = {}
    for match in re.finditer(r"\*\*(.*?)\*\*\s*\n\n(.*?)(?=\n\*\*|\n## |\Z)", markdown, flags=re.S):
        prompt = compact_markdown_key(match.group(1))
        answer = match.group(2).strip()
        if answer and answer != "_Нет ответа_":
            prompt_to_answer[prompt] = answer
    answers: dict[str, str] = {}
    for question in questions:
        answer = prompt_to_answer.get(compact_markdown_key(question.prompt))
        if answer:
            answers[question.key] = answer
    return answers


def update_history_context(user: dict[str, Any], max_chars: int = 22000) -> str:
    chunks: list[str] = []
    latest = repair_utf8_mojibake(str(user.get("last_update_markdown") or "").strip())
    if latest:
        title = user.get("last_update_filename") or "последний апдейт"
        chunks.append(f"# {title}\n\n{latest}")
    for path in saved_update_files(user):
        if sum(len(chunk) for chunk in chunks) >= max_chars:
            break
        try:
            text = read_markdown_file_text(path)
        except OSError:
            continue
        if text and text not in latest:
            chunks.append(f"# {path.name}\n\n{text}")
    combined = "\n\n---\n\n".join(chunks)
    return combined[:max_chars]


async def show_updates_list(update: Update, user: dict[str, Any]) -> None:
    items = stored_update_items(user)
    lines = [
        "<b>Мои апдейты</b>",
        "",
        "Апдейты отсортированы от самого позднего к самому раннему.",
    ]
    if items:
        lines.append("")
        for index, item in enumerate(items[:10], start=1):
            lines.append(update_item_line(index, item))
    else:
        lines.append("\nПока нет сохранённых апдейтов. Начни новый апдейт, и он появится здесь.")
    await reply(update, "\n".join(lines), reply_markup=updates_list_keyboard(user))


async def start_updates_chat(update: Update, user: dict[str, Any]) -> None:
    context = update_history_context(user, max_chars=4000)
    if not context.strip():
        await reply(
            update,
            "Пока нет апдейтов, по которым можно обсудить динамику. Начни новый апдейт, и я смогу сравнивать изменения.",
            reply_markup=updates_list_keyboard(user),
        )
        return
    store.update_user(user["telegram_user_id"], state="updates:chat", active_flow=None)
    await reply(
        update,
        "<b>Динамика апдейтов</b>\n\n"
        "Задай вопрос: например, «что повторяется?», «где я застреваю?» "
        "или «какая главная динамика по бизнесу за последние апдейты?»",
        reply_markup=back_to_updates_keyboard(),
    )


async def answer_updates_chat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    question: str,
) -> None:
    fresh = store.update_user(user["telegram_user_id"], state="updates:chat", active_flow=None)
    await reply(update, "Смотрю твои апдейты и динамику.")
    answer = await generate_updates_answer(fresh, question)
    await reply(
        update,
        f"{answer}\n\nМожно задать следующий вопрос по динамике или выйти назад.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Назад", callback_data="updates:menu")]]),
    )


async def generate_updates_answer(user: dict[str, Any], question: str) -> str:
    updates_context = update_history_context(user)
    if not updates_context:
        return "Пока нет сохранённых апдейтов для анализа динамики."
    if _openai is None:
        return (
            "OpenAI не настроен на сервере, поэтому сейчас не могу собрать динамический анализ. "
            "Апдейты сохранены, их можно скачать в .md и посмотреть отдельно."
        )
    system = (
        "Ты помогаешь участнику форум-группы анализировать динамику его апдейтов. "
        "Опирайся только на предоставленные апдейты. Пиши бережно, конкретно и без диагнозов."
    )
    prompt = (
        f"Апдейты пользователя:\n{updates_context}\n\n"
        f"Вопрос пользователя:\n{question[:4000]}\n\n"
        "Ответь кратко:\n"
        "1. Что видно по динамике\n"
        "2. Какие повторяющиеся паттерны или изменения заметны\n"
        "3. Один полезный следующий вопрос для дневника или форума"
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
        log.warning("updates answer failed user_id=%s error=%s", user.get("telegram_user_id"), exc)
        return "Не смог ответить по апдейтам сейчас. Попробуй ещё раз позже."


async def show_forum_guide(update: Update, user: dict[str, Any]) -> None:
    methodology = methodology_for_user(user)
    store.update_user(user["telegram_user_id"], state=None)
    await reply(
        update,
        "<b>Справочник форума</b>\n\n"
        "Я сохранил текстовые материалы в Markdown: общие принципы форума, "
        "формулу общения, правило 5%, окно Джохари, список чувств, "
        "классический Update и формат X-Competence.\n\n"
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
            [InlineKeyboardButton("Настроить напоминание", callback_data="diary:reminder")],
            [InlineKeyboardButton("Назад", callback_data="diary:menu")],
        ]
    )
    status = "включён" if enabled else "выключен"
    prompt_text = prompt or "пока не задан"
    reminder_text = user.get("diary_reminder_time") or "не настроено"
    await reply(
        update,
        f"<b>Режим дневника: {status}</b>\n\n"
        "Когда режим включён, свободные сообщения вне апдейта и health check "
        "я воспринимаю как дневниковые записи и даю обратную связь по твоему prompt.\n\n"
        f"<b>Напоминание</b>\n{esc(reminder_text)}\n\n"
        f"<b>Текущий prompt</b>\n{esc(prompt_text)}",
        reply_markup=keyboard,
    )


async def start_diary_entry(update: Update, user: dict[str, Any]) -> None:
    fields: dict[str, Any] = {"state": "diary:entry", "active_flow": None}
    if not user.get("diary_enabled"):
        fields.update(
            diary_enabled=1,
            diary_feedback_prompt=(user.get("diary_feedback_prompt") or DEFAULT_DIARY_PROMPT),
        )
    store.update_user(user["telegram_user_id"], **fields)
    await reply(
        update,
        "<b>Новая запись в дневнике</b>\n\n"
        "Пришли запись текстом или голосом. Я дам обратную связь по твоему prompt дневника.",
        reply_markup=back_to_diary_keyboard(),
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
        reply_markup=back_to_diary_keyboard(),
    )


async def save_diary_prompt(update: Update, user: dict[str, Any], text: str) -> None:
    prompt = text.strip()[:2000]
    if not prompt:
        await reply(update, "Prompt пустой. Напиши, какую обратную связь давать на дневник.")
        return
    updated = store.update_user(
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
        reply_markup=diary_menu_keyboard(updated),
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
    await reply(update, feedback or "Не смог собрать обратную связь сейчас. Запись принял.", reply_markup=diary_menu_keyboard(user))


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
    store.update_user(user["telegram_user_id"], active_flow=None, state=None)
    await reply(
        update,
        "<b>Как будем готовить апдейт?</b>\n\n"
        "Можно начать с чистого листа или открыть предыдущие ответы и добавлять к ним новые мысли.",
        reply_markup=update_start_keyboard(),
    )


async def begin_update_flow(
    update: Update,
    user: dict[str, Any],
    edit_previous: bool = False,
    source_selector: str | None = None,
) -> None:
    selected_markdown: str | None = None
    if edit_previous and source_selector is not None:
        selected_update = latest_update_markdown(user, source_selector)
        if selected_update is not None:
            selected_markdown = selected_update[0]
            selected_methodology = methodology_from_update_markdown(selected_markdown)
            if selected_methodology:
                user = store.update_user(user["telegram_user_id"], methodology=selected_methodology)
    elif edit_previous:
        previous_methodology = normalize_methodology(user.get("last_update_methodology"))
        if previous_methodology:
            user = store.update_user(user["telegram_user_id"], methodology=previous_methodology)

    methodology = methodology_for_user(user)
    questions = update_questions_for_user(user)
    answers: dict[str, str] = {}
    mode = "new"
    if edit_previous:
        previous_answers = parse_json_dict(user.get("last_update_answers")) if source_selector is None else {}
        if selected_markdown is not None:
            previous_answers = parse_update_markdown_answers(selected_markdown, questions)
        matching_answers = {
            key: str(value)
            for key, value in previous_answers.items()
            if key in {question.key for question in questions} and str(value).strip()
        }
        if matching_answers:
            answers = matching_answers
            mode = "edit"
        else:
            await reply(update, "Предыдущих ответов для этой методики не нашёл. Начинаем новый апдейт.")
    user = store.set_flow(user["telegram_user_id"], "update", 0, {"answers": answers, "mode": mode})
    await reply(
        update,
        f"<b>{'Изменяем предыдущий апдейт' if mode == 'edit' else 'Начинаем новый апдейт'}: {esc(methodology)}</b>\n\n"
        "Будем идти по всем вопросам. На один вопрос можно отправить несколько сообщений текстом или голосом. "
        "Я не перейду к следующему вопросу, пока ты не нажмёшь ➡️. "
        "Можно оставить вопрос без ответа и просто нажать ➡️. "
        "В конце я соберу Markdown-файл апдейта. На время сценария нижнее меню скрыто, "
        "чтобы его кнопки не попадали в ответы.",
        reply_markup=ReplyKeyboardRemove(),
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
        "Отвечай честно и конкретно. На один вопрос можно отправить несколько сообщений; "
        "к следующему вопросу я перейду только после кнопки ➡️. "
        "Можно оставить вопрос без ответа и просто нажать ➡️. "
        "В конце я соберу отчёт и попробую отправить "
        "его указанному Telegram-пользователю, если он уже запускал бота. "
        "На время сценария нижнее меню скрыто.",
        reply_markup=ReplyKeyboardRemove(),
    )
    await ask_current_question(update, user, HEALTH_QUESTIONS)


def post_forum_plan_intro() -> str:
    return (
        "<b>Личный план действий по разбору</b>\n\n"
        "Эта секция будет записана в выбранный файл апдейта последним разделом "
        "«Личный план действий».\n\n"
        "Можно отвечать текстом или голосом. На каждый вопрос можно отправить несколько сообщений, "
        "а к следующему вопросу я перейду только после кнопки ➡️.\n\n"
        "<b>Что фиксируем</b>\n"
        "1. Благодарность себе и другим: что важно не забыть проговорить?\n"
        "2. Действия в ближайшее время.\n\n"
        "<b>Для каждого действия укажи</b>\n"
        "- глагол совершенного вида;\n"
        "- где и когда;\n"
        "- с помощью чего;\n"
        "- какой результат;\n"
        "- срок."
    )


async def begin_post_forum_plan_flow(
    update: Update,
    user: dict[str, Any],
    source_selector: str | None = None,
) -> None:
    selected = latest_update_markdown(user, source_selector)
    if selected is None:
        await reply(
            update,
            "Не нашёл этот апдейт. Открой «Мои апдейты» и выбери файл ещё раз.",
            reply_markup=updates_menu_keyboard(user),
        )
        return
    markdown, filename = selected
    answers = parse_update_markdown_answers(markdown, POST_FORUM_PLAN_QUESTIONS)
    payload = {
        "answers": answers,
        "update_selector": source_selector,
        "update_filename": filename,
    }
    user = store.set_flow(user["telegram_user_id"], "post_forum_plan", 0, payload)
    await reply(update, post_forum_plan_intro(), reply_markup=ReplyKeyboardRemove())
    await ask_current_question(update, user, POST_FORUM_PLAN_QUESTIONS)


def question_message(question: Question, step: int, total: int, current_answer: str = "") -> str:
    message = (
        f"<b>{esc(question.section)}</b>\n"
        f"Вопрос {step + 1}/{total}\n\n"
        f"{esc(question.prompt)}"
    )
    answer = current_answer.strip()
    if answer:
        message += f"\n\n<b>Текущий ответ</b>\n<blockquote>{esc(clip(answer, 1600))}</blockquote>"
    return message


def flow_questions_for_user(user: dict[str, Any]) -> list[Question]:
    flow = user.get("active_flow")
    if flow == "update":
        return update_questions_for_user(user)
    if flow == "health":
        return HEALTH_QUESTIONS
    if flow == "post_forum_plan":
        return POST_FORUM_PLAN_QUESTIONS
    return []


async def ask_current_question(
    update: Update,
    user: dict[str, Any],
    questions: list[Question],
) -> None:
    step = int(user.get("active_step") or 0)
    if step >= len(questions):
        return
    question = questions[step]
    payload = store.payload(user)
    answers = payload.setdefault("answers", {})
    store.update_user(user["telegram_user_id"], state=None)
    await reply(
        update,
        question_message(question, step, len(questions), str(answers.get(question.key) or "")),
        reply_markup=flow_keyboard(False),
    )


async def handle_flow_next(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
) -> None:
    questions = flow_questions_for_user(user)
    if not questions:
        await reply(update, "Активного сценария нет. Выбери действие в меню.", reply_markup=MAIN_KEYBOARD)
        return
    step = int(user.get("active_step") or 0)
    if step >= len(questions):
        await finish_flow(update, context, user)
        return
    user = store.update_user(user["telegram_user_id"], active_step=step + 1, state=None)
    if step + 1 >= len(questions):
        await finish_flow(update, context, user)
        return
    await ask_current_question(update, user, questions)


async def handle_flow_back(update: Update, user: dict[str, Any]) -> None:
    questions = flow_questions_for_user(user)
    if not questions:
        await reply(update, "Активного сценария нет. Выбери действие в меню.", reply_markup=MAIN_KEYBOARD)
        return

    step = int(user.get("active_step") or 0)
    target_step = max(step - 1, 0)
    user = store.update_user(user["telegram_user_id"], active_step=target_step, state=None)
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
    had_answer = bool(str(answers.get(question.key) or "").strip())
    answers[question.key] = append_answer_text(answers.get(question.key, ""), text)
    user = store.update_user(
        user["telegram_user_id"],
        flow_payload=json.dumps(payload, ensure_ascii=False),
        state="flow:await_next",
    )

    verb = "Добавил к ответу" if had_answer else "Записал"
    await reply(
        update,
        f"{verb}. Можно прислать ещё сообщение к этому вопросу или нажать ➡️.",
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
    elif flow == "post_forum_plan":
        await finish_post_forum_plan(update, user, answers)
    store.set_flow(user["telegram_user_id"], None)


def answers_by_section(answers: dict[str, str], questions: list[Question]) -> dict[str, list[tuple[str, str]]]:
    grouped: dict[str, list[tuple[str, str]]] = {}
    for question in questions:
        grouped.setdefault(question.section, []).append((question.prompt, answers.get(question.key, "")))
    return grouped


def append_answer_text(existing: str, text: str) -> str:
    clean_existing = str(existing or "").strip()
    clean_text = text.strip()
    if not clean_existing:
        return clean_text
    if not clean_text:
        return clean_existing
    return f"{clean_existing}\n\n{clean_text}"


def parse_json_dict(value: Any) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def build_update_markdown(
    user: dict[str, Any],
    answers: dict[str, str],
    reflection: str = "",
    questions: list[Question] | None = None,
) -> str:
    forum_date = format_forum_date(user.get("next_forum_date")) or "не указана"
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


def personal_plan_answers_filled(answers: dict[str, str]) -> bool:
    return any(answer_text_is_filled(str(answers.get(question.key) or "")) for question in POST_FORUM_PLAN_QUESTIONS)


def build_personal_plan_section(answers: dict[str, str]) -> str:
    if not personal_plan_answers_filled(answers):
        return ""
    lines = ["## Личный план действий", ""]
    for question in POST_FORUM_PLAN_QUESTIONS:
        answer = str(answers.get(question.key) or "").strip()
        if not answer_text_is_filled(answer):
            continue
        lines.extend([f"**{question.prompt}**", "", answer, ""])
    return "\n".join(lines).strip() + "\n"


def replace_personal_plan_section(markdown: str, answers: dict[str, str]) -> str:
    text = repair_utf8_mojibake(str(markdown or "").lstrip("\ufeff")).strip()
    text = re.sub(r"\n*^##\s+Личный план действий\s*$\n.*?(?=^##\s+|\Z)", "\n", text, flags=re.M | re.S).strip()
    section = build_personal_plan_section(answers).strip()
    if section:
        text = f"{text}\n\n{section}"
    return text.strip() + "\n"


def question_list_markdown(title: str, questions: list[Question]) -> list[str]:
    lines = [f"## {title}", ""]
    current_section = ""
    for index, question in enumerate(questions, start=1):
        if question.section != current_section:
            current_section = question.section
            lines.extend([f"### {current_section}", ""])
        lines.append(f"{index}. {question.prompt}")
    lines.append("")
    return lines


def build_ai_forum_standard_markdown(user: dict[str, Any]) -> str:
    methodology = methodology_for_user(user)
    selected_questions = update_questions_for_user(user)
    guide_context = load_forum_guide_context(None, max_chars=60000)
    lines = [
        "# Стандарт форума и инструкция для ИИ-агента",
        "",
        f"- Выбранный формат: {methodology}",
        f"- Сформировано: {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Назначение файла",
        "",
        "Этот Markdown-файл можно передать ИИ-агенту, чтобы он провёл пользователя по подготовке форумного апдейта целиком. "
        "Агент должен опираться на правила форума, выбранную методику и список вопросов ниже.",
        "",
        "## Инструкция для ИИ-агента",
        "",
        "1. Работай на русском языке, коротко и бережно.",
        "2. Сначала уточни ФИО, бизнес-клуб, название форум-группы и дату следующего форума, если пользователь хочет включить это в итоговый файл.",
        "3. Объясни базовые правила форума: говорить только из личного опыта, не давать советов, не анализировать других, держать конфиденциальность.",
        "4. Проводи пользователя по вопросам выбранного формата строго по одному вопросу за раз.",
        "5. После каждого ответа фиксируй его в структуре Markdown и показывай прогресс в формате `заполнено N/TOTAL`.",
        "6. Если пользователь просит пропустить вопрос, оставь в ответе пустое значение `_Нет ответа_` и переходи дальше.",
        "7. Если пользователь просит вернуться назад, покажи предыдущий вопрос и дай заменить ответ.",
        "8. Помогай формулировать личный опыт, чувства и главный запрос, но не придумывай факты за пользователя.",
        "9. При оценке апдейта проверяй: личный опыт, конкретику, чувства, глубину 5%, отсутствие советов и ясность вопроса к форуму.",
        "10. В конце выдай один Markdown-файл форумного апдейта с секциями выбранного формата и короткой сводкой.",
        "",
        "## Выбранная последовательность вопросов",
        "",
    ]
    lines.extend(question_list_markdown(methodology, selected_questions))
    lines.extend(question_list_markdown("Классическая методика (YPO)", CLASSIC_UPDATE_QUESTIONS))
    lines.extend(question_list_markdown("С личной стратегией (X-Competence)", UPDATE_QUESTIONS))
    lines.extend(question_list_markdown("Постфорумный личный план действий", POST_FORUM_PLAN_QUESTIONS))
    lines.extend(
        [
            "## Справочник и методологические материалы",
            "",
            guide_context.strip() or "_Материалы справочника не найдены._",
            "",
        ]
    )
    return "\n".join(lines).strip() + "\n"


async def send_ai_forum_standard_file(update: Update, user: dict[str, Any]) -> None:
    if not update.effective_message or not update.effective_chat:
        return
    content = build_ai_forum_standard_markdown(user)
    filename = f"forum-standard-for-ai-{datetime.now(TZ).strftime('%Y%m%d-%H%M')}.md"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as fh:
            fh.write(markdown_bytes_for_download(content))
            temp_path = Path(fh.name)
        with temp_path.open("rb") as fh:
            await spaced_bot_send(
                update.effective_chat.id,
                lambda: update.effective_message.reply_document(
                    document=fh,
                    filename=filename,
                    caption="MD-файл для ИИ-агента: стандарт форума, методология и алгоритм заполнения апдейта.",
                ),
            )
    finally:
        if temp_path:
            temp_path.unlink(missing_ok=True)


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
    store.update_user(
        user["telegram_user_id"],
        last_update_answers=json.dumps(answers, ensure_ascii=False),
        last_update_markdown=content,
        last_update_filename=filename,
        last_update_methodology=methodology_for_user(user),
        last_update_at=now_iso(),
    )
    path = user_dir / filename
    path.write_bytes(markdown_bytes_for_download(content))
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


async def finish_post_forum_plan(update: Update, user: dict[str, Any], answers: dict[str, str]) -> None:
    payload = store.payload(user)
    store.update_user(
        user["telegram_user_id"],
        last_post_forum_plan_answers=json.dumps(answers, ensure_ascii=False),
        last_post_forum_plan_at=now_iso(),
    )
    source_selector = payload.get("update_selector")
    update_filename = str(payload.get("update_filename") or "")
    if source_selector is not None and update_filename:
        selected = latest_update_markdown(user, str(source_selector))
        if selected is None:
            await reply(
                update,
                "Личный план действий сохранил в профиле, но выбранный файл апдейта уже не нашёл.",
                reply_markup=MAIN_KEYBOARD,
            )
            return
        markdown, filename = selected
        updated_markdown = replace_personal_plan_section(markdown, answers)
        write_selected_update_markdown(user, str(source_selector), updated_markdown, filename)
        if personal_plan_answers_filled(answers):
            await reply(
                update,
                "Личный план действий записал в файл апдейта последней секцией. Теперь можно скачать обновлённый .md или .html.",
                reply_markup=MAIN_KEYBOARD,
            )
        else:
            await reply(
                update,
                "Личный план действий пустой — убрал эту секцию из файла апдейта.",
                reply_markup=MAIN_KEYBOARD,
            )
        return
    await reply(
        update,
        "Личный план действий зафиксировал. Следующим шагом спрошу здоровье форум-группы.",
        reply_markup=MAIN_KEYBOARD,
    )


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

    await reply(update, transcript_message(transcript))
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


async def post_forum_plan_maintenance(context: ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.now(TZ).date()
    for user in store.complete_users():
        if not is_profile_complete(user):
            continue
        try:
            await maybe_send_post_forum_plan(context, user, today)
        except Exception:
            log.exception("post-forum plan maintenance failed for user_id=%s", user.get("telegram_user_id"))


async def maybe_send_post_forum_plan(
    context: ContextTypes.DEFAULT_TYPE,
    user: dict[str, Any],
    today: date,
) -> None:
    value = user.get("next_forum_date")
    if not value:
        return
    forum_date = date.fromisoformat(value)
    if today != forum_date + timedelta(days=1):
        return
    if store.reminder_sent(user["telegram_user_id"], "post_forum_plan", value):
        return
    fresh = store.get_user(user["telegram_user_id"]) or user
    chat_id = int(fresh["chat_id"])
    if fresh.get("active_flow"):
        await safe_send(
            context,
            chat_id,
            "Перед health check нужно зафиксировать личный план действий, но у тебя уже открыт другой сценарий. "
            "Закончи его или нажми /cancel.",
        )
        store.mark_reminder(user["telegram_user_id"], "post_forum_plan", value)
        return
    fresh = store.set_flow(user["telegram_user_id"], "post_forum_plan", 0, {"answers": {}})
    await safe_send(
        context,
        chat_id,
        post_forum_plan_intro(),
        reply_markup=ReplyKeyboardRemove(),
    )
    await safe_send(
        context,
        chat_id,
        question_message(POST_FORUM_PLAN_QUESTIONS[0], 0, len(POST_FORUM_PLAN_QUESTIONS)),
        reply_markup=flow_keyboard(False),
    )
    store.mark_reminder(user["telegram_user_id"], "post_forum_plan", value)


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
                methodology = methodology_for_user(fresh)
                await safe_send(
                    context,
                    chat_id,
                    "<b>Пора готовить форумный апдейт</b>\n\n"
                    f"До форума {days_left} дн. Методика: {esc(methodology)}.\n\n"
                    "Выбери: начать новый апдейт или изменить предыдущий.",
                    reply_markup=ReplyKeyboardRemove(),
                )
                await safe_send(
                    context,
                    chat_id,
                    "Как будем готовить?",
                    reply_markup=update_start_keyboard(),
                )
            else:
                await safe_send(
                    context,
                    chat_id,
                    "<b>Пора готовить форумный апдейт</b>\n\n"
                    "У тебя уже открыт другой сценарий. Закончи его или нажми /cancel, "
                    "потом выбери «Апдейты».",
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
                    "Давай зафиксируем здоровье форум-группы. На один вопрос можно отправить несколько сообщений; "
                    "к следующему вопросу я перейду только после кнопки ➡️. Первый вопрос:",
                    reply_markup=ReplyKeyboardRemove(),
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


async def diary_reminder_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    slot = str(context.job.data)
    reminder_time = DIARY_REMINDER_CHOICES.get(slot, DIARY_REMINDER_CHOICES["21"])[0]
    today = datetime.now(TZ).date()
    for user in store.complete_users():
        if not user.get("diary_enabled") or user.get("diary_reminder_time") != reminder_time:
            continue
        reminder_type = f"diary_{slot}"
        reminder_key = today.isoformat()
        if store.reminder_sent(user["telegram_user_id"], reminder_type, reminder_key):
            continue
        if slot == "08":
            text = (
                "<b>Дневник</b>\n\n"
                "Доброе утро. Если вчера был важный материал для апдейта, надиктуй или напиши дневниковую запись. "
                "Я сохраню её как дневник и дам обратную связь по твоему prompt."
            )
        else:
            text = (
                "<b>Дневник</b>\n\n"
                "Вечерняя отметка. Если сегодня было что-то важное для апдейта или жизни, "
                "надиктуй или напиши дневниковую запись. Я дам обратную связь по твоему prompt."
            )
        ok = await safe_send(context, int(user["chat_id"]), text)
        if ok:
            store.mark_reminder(user["telegram_user_id"], reminder_type, reminder_key)


async def post_init(application: Application) -> None:
    migrated = migrate_saved_update_markdown_files()
    if migrated:
        log.info("migrated saved markdown updates to utf-8 bom count=%s", migrated)
    maintenance_time = parse_time(DAILY_MAINTENANCE_TIME)
    post_forum_plan_time = time_minus_minutes(maintenance_time, 30)
    application.job_queue.run_daily(
        post_forum_plan_maintenance,
        time=post_forum_plan_time,
        name="post-forum-plan-maintenance",
    )
    application.job_queue.run_daily(daily_maintenance, time=maintenance_time, name="daily-maintenance")
    application.job_queue.run_daily(diary_reminder_job, time=parse_time("21:00"), data="21", name="diary-reminder-21")
    application.job_queue.run_daily(diary_reminder_job, time=parse_time("08:00"), data="08", name="diary-reminder-08")
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
