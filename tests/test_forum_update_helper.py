import importlib
import os
from datetime import date

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DATA_DIR", "/tmp/forum-update-helper-tests")

bot = importlib.import_module("main")


def test_parse_forum_date_ru_numeric():
    parsed = bot.parse_forum_date("23.06.2026", base=date(2026, 5, 27))

    assert parsed == date(2026, 6, 23)


def test_parse_forum_date_short_day_month_current_or_next_year():
    assert bot.parse_forum_date("2.06", base=date(2026, 5, 28)) == date(2026, 6, 2)
    assert bot.parse_forum_date("14.03", base=date(2026, 5, 28)) == date(2027, 3, 14)


def test_update_question_counter_message():
    message = bot.question_message(bot.UPDATE_QUESTIONS[3], 3, len(bot.UPDATE_QUESTIONS))

    assert "Заполнено: <b>3/" in message
    assert "Вопрос 4/" in message


def test_rating_questions_warn_about_neutral_seven():
    assert "оценка 7 коварная" in bot.UPDATE_QUESTIONS[0].prompt
    assert "оценка 7 коварная" in bot.CLASSIC_UPDATE_QUESTIONS[0].prompt


def test_x_competence_rating_question_merges_previous_month_and_change():
    assert "оценку этого месяца" in bot.UPDATE_QUESTIONS[0].prompt
    assert "оценку предыдущего месяца" in bot.UPDATE_QUESTIONS[0].prompt
    assert "что изменилось" in bot.UPDATE_QUESTIONS[0].prompt
    assert not any(question.key.startswith("changed_") for question in bot.UPDATE_QUESTIONS)


def test_append_answer_text_preserves_multiple_messages():
    assert bot.append_answer_text("", "первый фрагмент") == "первый фрагмент"
    assert bot.append_answer_text("первый фрагмент", "второй фрагмент") == "первый фрагмент\n\nвторой фрагмент"
    assert bot.append_answer_text("первый фрагмент", "   ") == "первый фрагмент"


def test_flow_keeps_step_until_next_and_appends_messages(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    replies = []

    async def fake_reply(_update, text, **_kwargs):
        replies.append(text)

    monkeypatch.setattr(bot, "reply", fake_reply)
    now = bot.now_iso()
    test_store.conn.execute(
        """
        INSERT INTO users (
            telegram_user_id, chat_id, active_flow, active_step, flow_payload,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (123, 456, "update", 0, "{}", now, now),
    )
    test_store.conn.commit()
    questions = [bot.Question("q1", "Первый вопрос", "Секция"), bot.Question("q2", "Второй вопрос", "Секция")]

    bot.asyncio.run(bot.handle_question_answer(None, None, test_store.get_user(123), "первый фрагмент", questions))
    user = test_store.get_user(123)
    bot.asyncio.run(bot.handle_question_answer(None, None, user, "второй фрагмент", questions))

    user = test_store.get_user(123)
    payload = test_store.payload(user)
    assert user["active_step"] == 0
    assert user["state"] == "flow:await_next"
    assert payload["answers"]["q1"] == "первый фрагмент\n\nвторой фрагмент"
    assert "Добавил к ответу" in replies[-1]


def test_parse_answer_check_json():
    assert bot.parse_answer_check_json('{"answered": true, "missing": ""}') == {"ok": True, "missing": ""}
    assert bot.parse_answer_check_json('{"answered": "false", "missing": "нет ответа"}') == {
        "ok": False,
        "missing": "нет ответа",
    }
    assert bot.parse_answer_check_json('```json\n{"answered": false, "missing": "нет оценки прошлого месяца"}\n```') == {
        "ok": False,
        "missing": "нет оценки прошлого месяца",
    }


def test_answer_check_rejects_empty_without_openai(monkeypatch):
    monkeypatch.setattr(bot, "_openai", None)
    result = bot.asyncio.run(bot.check_question_answer(bot.UPDATE_QUESTIONS[0], " "))

    assert result["ok"] is False
    assert "пустой" in result["missing"]


def test_flow_next_does_not_skip_unanswered_question(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    replies = []

    async def fake_reply(_update, text, **_kwargs):
        replies.append(text)

    monkeypatch.setattr(bot, "reply", fake_reply)
    now = bot.now_iso()
    test_store.conn.execute(
        """
        INSERT INTO users (
            telegram_user_id, chat_id, active_flow, active_step, flow_payload,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (123, 456, "update", 0, "{}", now, now),
    )
    test_store.conn.commit()

    bot.asyncio.run(bot.handle_flow_next(None, None, test_store.get_user(123)))

    user = test_store.get_user(123)
    assert user["active_step"] == 0
    assert "Сначала ответь" in replies[-1]


def test_build_update_markdown_contains_sections():
    user = {
        "forum_group": "High Level",
        "full_name": "Иван Иванов",
        "business_club": "Эквиум",
        "methodology": "С личной стратегией (X-Competence)",
        "next_forum_date": "2026-06-23",
    }
    answers = {bot.UPDATE_QUESTIONS[0].key: "8/10, стало спокойнее"}

    md = bot.build_update_markdown(user, answers)

    assert "# Форум-апдейт — High Level" in md
    assert "- Методика: С личной стратегией (X-Competence)" in md
    assert "Часть 1. Оценка трёх сфер" in md
    assert "8/10" in md


def test_classic_methodology_selects_classic_questions():
    user = {"methodology": "Классическая (YPO)"}

    assert bot.methodology_for_user({}) == "Классическая (YPO)"
    assert bot.methodology_for_user({"methodology": "YPO"}) == "Классическая (YPO)"
    assert bot.update_questions_for_user(user) == bot.CLASSIC_UPDATE_QUESTIONS
    assert bot.CLASSIC_UPDATE_QUESTIONS[0].prompt.startswith("Бизнес")


def test_strategy_methodology_selects_x_competence_questions():
    user = {"methodology": "С личной стратегией (X-Competence)"}

    assert bot.update_questions_for_user(user) == bot.UPDATE_QUESTIONS


def test_methodology_keyboard_has_no_skip_button():
    keyboard = bot.methodology_keyboard().inline_keyboard
    callback_data = [button.callback_data for row in keyboard for button in row]

    assert "skip:methodology" not in callback_data
    assert set(callback_data) == {"methodology:classic", "methodology:strategy"}
    assert all(len(value.encode("utf-8")) <= 64 for value in callback_data)


def test_ai_agent_keyboard_offers_md_or_bot_flow():
    labels = [button.text for row in bot.ai_agent_keyboard().inline_keyboard for button in row]

    assert labels == ["Сложно: загрузить только файл", "Просто: продолжить работу"]
    assert bot.ONBOARDING_TOTAL_STEPS == 9


def test_diary_reminder_keyboard_has_onboarding_choices():
    labels = [button.text for row in bot.diary_reminder_keyboard().inline_keyboard for button in row]

    assert labels == [
        "Не включать дневник",
        "Включить: 21:00 этого дня",
        "Включить: 08:00 следующего дня",
    ]


def test_onboarding_finished_keyboard_has_single_prepare_action():
    buttons = [button for row in bot.onboarding_finished_keyboard().inline_keyboard for button in row]

    assert [button.text for button in buttons] == ["Подготовить апдейт"]
    assert [button.callback_data for button in buttons] == ["menu:update"]


def test_main_menu_uses_information_submenu():
    main_buttons = [button.text for row in bot.MAIN_KEYBOARD.keyboard for button in row]
    inline_callbacks = [
        button.callback_data
        for row in bot.main_inline_keyboard().inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert "Информация" in main_buttons
    assert "О боте" not in main_buttons
    assert "Ищу психолога" not in main_buttons
    assert "menu:info" in inline_callbacks


def test_information_submenu_contains_info_actions():
    labels = [button.text for row in bot.info_inline_keyboard().inline_keyboard for button in row]

    assert "О боте" in labels
    assert "Ищу психолога" in labels
    assert "Ищу ментора" in labels
    assert "Связаться с автором" in labels
    assert "Назад в меню" in labels


def test_onboarding_existing_value_buttons():
    labels = [
        button.text
        for row in bot.skip_keyboard("full_name", {"full_name": "Андрей Путин"}).inline_keyboard
        for button in row
    ]

    assert "Андрей Путин" in labels
    assert "Пропустить" in labels


def test_onboarding_formats_existing_forum_date_button():
    labels = [
        button.text
        for row in bot.skip_keyboard("next_forum_date", {"next_forum_date": "2026-06-26"}).inline_keyboard
        for button in row
    ]

    assert "26.06.2026" in labels
    assert "2026-06-26" not in labels


def test_community_report_skip_button_is_explicit():
    labels = [button.text for row in bot.skip_keyboard("community_chat").inline_keyboard for button in row]

    assert labels == ["Никому не отправлять"]


def test_profile_complete_allows_empty_optional_identity_fields():
    user = {
        "business_club": "",
        "full_name": "",
        "forum_group": "",
        "keep_files": 0,
        "next_forum_date": "2026-06-02",
    }

    assert bot.is_profile_complete(user)


def test_keep_files_keyboard_has_no_skip_or_duplicate_current_button():
    labels = [button.text for row in bot.keep_files_keyboard({"keep_files": 1}).inline_keyboard for button in row]

    assert labels.count("Сохранять") == 0
    assert "Сохранять (рекомендуем, текущее)" in labels
    assert "Удалять" in labels
    assert "Пропустить" not in labels


def test_submenus_include_back_buttons():
    profile_labels = [button.text for row in bot.profile_cabinet_keyboard().inline_keyboard for button in row]
    guide_labels = [button.text for row in bot.guide_keyboard().inline_keyboard for button in row]
    edit_labels = [
        button.text
        for row in bot.business_club_keyboard(prefix="profile:club").inline_keyboard
        for button in row
    ]

    assert "Назад" in profile_labels
    assert "Назад" in guide_labels
    assert "Назад" in edit_labels


def test_flow_keyboard_uses_native_next_and_back_actions():
    keyboard = bot.flow_keyboard().inline_keyboard
    labels = [button.text for row in keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard for button in row]

    assert labels == ["Назад", "Далее"]
    assert callbacks == ["flow:back", "flow:next"]
    assert "Отменить сценарий" not in labels


def test_profile_has_download_files_button():
    labels = [button.text for row in bot.profile_cabinet_keyboard().inline_keyboard for button in row]

    assert "Загрузить мои файлы" in labels


def test_saved_update_files_returns_markdown_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "UPDATES_DIR", tmp_path)
    user = {"telegram_user_id": 123}
    user_dir = tmp_path / "123"
    user_dir.mkdir()
    older = user_dir / "older.md"
    newer = user_dir / "newer.md"
    older.write_text("older", encoding="utf-8")
    newer.write_text("newer", encoding="utf-8")

    files = bot.saved_update_files(user)

    assert files[0].name == "newer.md"
    assert {path.name for path in files} == {"older.md", "newer.md"}


def test_forum_guide_context_loads_materials():
    context = bot.load_forum_guide_context("Классическая (YPO)")

    assert "Классическая методика" in context
    assert "Форум — это" in context


def test_ai_forum_standard_markdown_contains_agent_instruction():
    text = bot.build_ai_forum_standard_markdown({"methodology": "С личной стратегией (X-Competence)"})

    assert "# Стандарт форума и инструкция для ИИ-агента" in text
    assert "## Инструкция для ИИ-агента" in text
    assert "Классическая методика (YPO)" in text
    assert "С личной стратегией (X-Competence)" in text
    assert "заполнено N/TOTAL" in text


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
    assert "diary_reminder_time" in columns


def test_apply_diary_reminder_choice_sets_default_prompt(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    now = bot.now_iso()
    test_store.conn.execute(
        """
        INSERT INTO users (
            telegram_user_id, chat_id, created_at, updated_at
        ) VALUES (?, ?, ?, ?)
        """,
        (123, 456, now, now),
    )
    test_store.conn.commit()

    updated = bot.apply_diary_reminder_choice(test_store.get_user(123), "21")

    assert updated["diary_enabled"] == 1
    assert updated["diary_reminder_time"] == "21:00"
    assert updated["diary_feedback_prompt"] == bot.DEFAULT_DIARY_PROMPT


def test_store_resolves_user_by_username(tmp_path):
    store = bot.Store(tmp_path / "state.sqlite3")
    now = bot.now_iso()
    store.conn.execute(
        """
        INSERT INTO users (
            telegram_user_id, chat_id, username, full_name, business_club,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (123, 456, "utandr", "Андрей Путин", "Другое", now, now),
    )
    store.conn.commit()

    assert store.get_user_by_username("@utandr")["chat_id"] == 456
    assert store.get_user_by_username("UTANDR")["telegram_user_id"] == 123


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


def test_profile_cabinet_text_marks_empty_report_recipient():
    text = bot.profile_cabinet_text(
        {
            "business_club": "Другое",
            "full_name": "Участник форума 777",
            "forum_group": "Форум-группа 777",
            "methodology": "Классическая (YPO)",
            "community_chat": "",
            "keep_files": 0,
            "next_forum_date": "2026-06-26",
            "diary_enabled": 0,
            "diary_reminder_time": None,
        }
    )

    assert "Личный кабинет" in text
    assert "Методика" in text
    assert "Классическая (YPO)" in text
    assert "Получатель отчётов" in text
    assert "отчёты остаются в личном чате" in text
    assert "Следующий форум: <b>26.06.2026</b>" in text
    assert "2026-06-26" not in text
