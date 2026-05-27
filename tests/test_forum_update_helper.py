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
    assert "Часть 1. Оценка трёх сфер" in md
    assert "8/10" in md


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
