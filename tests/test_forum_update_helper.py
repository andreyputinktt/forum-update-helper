import importlib
import os
from datetime import date

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DATA_DIR", "/tmp/forum-update-helper-tests")

bot = importlib.import_module("main")


def test_parse_forum_date_ru_numeric():
    parsed = bot.parse_forum_date("23.06.2026", base=date(2026, 5, 27))

    assert parsed == date(2026, 6, 23)


def test_update_question_counter_message():
    message = bot.question_message(bot.UPDATE_QUESTIONS[3], 3, len(bot.UPDATE_QUESTIONS))

    assert "Заполнено: <b>3/" in message
    assert "Вопрос 4/" in message


def test_build_update_markdown_contains_sections():
    user = {
        "forum_group": "High Level",
        "full_name": "Иван Иванов",
        "business_club": "Эквиум",
        "next_forum_date": "2026-06-23",
    }
    answers = {bot.UPDATE_QUESTIONS[0].key: "8/10, стало спокойнее"}

    md = bot.build_update_markdown(user, answers)

    assert "# Форум-апдейт — High Level" in md
    assert "- Методика: YPO" in md
    assert "Часть 1. Оценка трёх сфер" in md
    assert "8/10" in md


def test_classic_methodology_selects_classic_questions():
    user = {"methodology": "Классическая"}

    assert bot.methodology_for_user({}) == "YPO"
    assert bot.update_questions_for_user(user) == bot.CLASSIC_UPDATE_QUESTIONS
    assert bot.CLASSIC_UPDATE_QUESTIONS[0].prompt.startswith("Бизнес")


def test_forum_guide_context_loads_materials():
    context = bot.load_forum_guide_context("Классическая")

    assert "Классическая методика" in context
    assert "Форум — это" in context


def test_store_delete_user_removes_records(tmp_path):
    store = bot.Store(tmp_path / "state.sqlite3")
    now = bot.now_iso()
    store.conn.execute(
        """
        INSERT INTO users (
            telegram_user_id, chat_id, full_name, business_club,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (123, 456, "Иван Иванов", "К1", now, now),
    )
    store.conn.execute(
        "INSERT INTO interactions (telegram_user_id, kind, created_at) VALUES (?, ?, ?)",
        (123, "text", now),
    )
    store.conn.commit()

    store.delete_user(123)

    assert store.get_user(123) is None
    assert store.conn.execute("SELECT COUNT(*) AS n FROM interactions").fetchone()["n"] == 0


def test_store_adds_diary_columns(tmp_path):
    store = bot.Store(tmp_path / "state.sqlite3")
    columns = {
        row["name"]
        for row in store.conn.execute("PRAGMA table_info(users)").fetchall()
    }

    assert "diary_enabled" in columns
    assert "diary_feedback_prompt" in columns


def test_diary_feedback_without_openai(monkeypatch):
    monkeypatch.setattr(bot, "_openai", None)
    text = bot.asyncio.run(
        bot.generate_diary_feedback(
            {"full_name": "Иван Иванов", "forum_group": "High Level"},
            "Сегодня много думал про лидерство.",
            "С точки зрения лидерства.",
        )
    )

    assert "OpenAI не настроен" in text


def test_profile_defaults_are_generated():
    user = {"telegram_user_id": 777}

    assert bot.default_full_name(user) == "Участник форума 777"
    assert bot.default_forum_group(user) == "Форум-группа 777"


def test_profile_cabinet_text_marks_empty_community():
    text = bot.profile_cabinet_text(
        {
            "business_club": "Другое",
            "full_name": "Участник форума 777",
            "forum_group": "Форум-группа 777",
            "methodology": "Классическая",
            "community_chat": "",
            "keep_files": 0,
            "next_forum_date": "2026-06-26",
            "diary_enabled": 0,
        }
    )

    assert "Личный кабинет" in text
    assert "Методика" in text
    assert "Классическая" in text
    assert "отчёты остаются в личном чате" in text
