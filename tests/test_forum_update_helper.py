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


def test_time_minus_minutes_for_post_forum_plan():
    assert bot.time_minus_minutes(bot.parse_time("09:30"), 30).strftime("%H:%M") == "09:00"


def test_update_question_counter_message():
    message = bot.question_message(bot.UPDATE_QUESTIONS[3], 3, len(bot.UPDATE_QUESTIONS))

    assert "Заполнено:" not in message
    assert "Вопрос 4/" in message


def test_large_update_questions_use_readable_multiline_format():
    message = bot.question_message(bot.UPDATE_QUESTIONS[0], 0, len(bot.UPDATE_QUESTIONS))

    assert "\n- оценку этого месяца" in message
    assert "\n- оценку предыдущего месяца" in message
    assert "\n\n" in message


def test_rating_questions_warn_about_neutral_seven():
    assert "оценка 7 коварная" in bot.UPDATE_QUESTIONS[0].prompt
    assert "оценка 7 коварная" in bot.CLASSIC_UPDATE_QUESTIONS[0].prompt


def test_x_competence_rating_question_merges_previous_month_and_change():
    assert "оценку этого месяца" in bot.UPDATE_QUESTIONS[0].prompt
    assert "оценку предыдущего месяца" in bot.UPDATE_QUESTIONS[0].prompt
    assert "что изменилось" in bot.UPDATE_QUESTIONS[0].prompt
    assert "что дало наибольший вклад" in bot.UPDATE_QUESTIONS[0].prompt
    assert not any(question.key.startswith("changed_") for question in bot.UPDATE_QUESTIONS)


def test_x_competence_questions_are_condensed_by_sphere():
    keys = [question.key for question in bot.UPDATE_QUESTIONS]

    assert len(bot.UPDATE_QUESTIONS) == 16
    assert not any(key.startswith("impact_") for key in keys)
    assert not any(key.startswith("delta_") for key in keys)
    assert not any(key.startswith("past_action_") for key in keys)
    assert not any(key.startswith("past_failed_") for key in keys)
    assert not any(key.startswith("annual_goal_") for key in keys)
    assert "meeting_gratitude" not in keys
    assert "next_actions" not in keys
    assert "retrospective_Моё дело" in keys
    assert "next_period_Моё дело" in keys
    assert [question.key for question in bot.POST_FORUM_PLAN_QUESTIONS] == ["meeting_gratitude", "next_actions"]


def test_transcript_message_is_formatted_for_telegram():
    text = bot.transcript_message(
        "Первое предложение. Второе предложение. Третье предложение с <опасным> текстом."
    )

    assert text.startswith("<b>Транскрипт</b>\n\n<blockquote>")
    assert "Первое предложение. Второе предложение." in text
    assert "\n\nТретье предложение" in text
    assert "&lt;опасным&gt;" in text


def test_question_message_shows_existing_answer_when_editing():
    message = bot.question_message(bot.UPDATE_QUESTIONS[0], 0, len(bot.UPDATE_QUESTIONS), "старый ответ")

    assert "Текущий ответ" in message
    assert "старый ответ" in message


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


def test_edit_previous_update_preloads_answers(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    replies = []

    async def fake_reply(_update, text, **_kwargs):
        replies.append(text)

    monkeypatch.setattr(bot, "reply", fake_reply)
    now = bot.now_iso()
    first_question = bot.UPDATE_QUESTIONS[0]
    test_store.conn.execute(
        """
        INSERT INTO users (
            telegram_user_id, chat_id, methodology, last_update_answers, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            123,
            456,
            "С личной стратегией (X-Competence)",
            bot.json.dumps({first_question.key: "старый ответ"}, ensure_ascii=False),
            now,
            now,
        ),
    )
    test_store.conn.commit()

    bot.asyncio.run(bot.begin_update_flow(None, test_store.get_user(123), edit_previous=True))

    user = test_store.get_user(123)
    payload = test_store.payload(user)
    assert payload["mode"] == "edit"
    assert payload["answers"][first_question.key] == "старый ответ"
    assert "Текущий ответ" in replies[-1]


def test_finish_post_forum_plan_writes_selected_update_file(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    monkeypatch.setattr(bot, "UPDATES_DIR", tmp_path / "updates")
    replies = []

    async def fake_reply(_update, text, **_kwargs):
        replies.append(text)

    monkeypatch.setattr(bot, "reply", fake_reply)
    user_dir = bot.UPDATES_DIR / "123"
    user_dir.mkdir(parents=True)
    update_path = user_dir / "forum-update-20260531-1200.md"
    update_path.write_text("# Апдейт\n\n## Я\n\n**Вопрос**\n\nОтвет\n", encoding="utf-8")
    now = bot.now_iso()
    test_store.conn.execute(
        """
        INSERT INTO users (
            telegram_user_id, chat_id, active_flow, active_step, flow_payload,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            123,
            456,
            "post_forum_plan",
            1,
            bot.json.dumps(
                {"answers": {}, "update_selector": "0", "update_filename": update_path.name},
                ensure_ascii=False,
            ),
            now,
            now,
        ),
    )
    test_store.conn.commit()

    bot.asyncio.run(
        bot.finish_post_forum_plan(
            None,
            test_store.get_user(123),
            {"meeting_gratitude": "Поблагодарил группу.", "next_actions": "Записал выводы завтра утром."},
        )
    )

    updated = bot.read_markdown_file_text(update_path)
    assert "## Личный план действий" in updated
    assert updated.rstrip().endswith("Записал выводы завтра утром.")
    assert "записал в файл апдейта" in replies[-1]


def test_begin_post_forum_plan_flow_sends_single_intro_message(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    monkeypatch.setattr(bot, "UPDATES_DIR", tmp_path / "updates")
    replies = []

    async def fake_reply(_update, text, **_kwargs):
        replies.append(text)

    monkeypatch.setattr(bot, "reply", fake_reply)
    user_dir = bot.UPDATES_DIR / "123"
    user_dir.mkdir(parents=True)
    update_path = user_dir / "forum-update-20260531-1200.md"
    update_path.write_text("# Апдейт\n\n## Я\n\n**Вопрос**\n\nОтвет\n", encoding="utf-8")
    now = bot.now_iso()
    test_store.conn.execute(
        """
        INSERT INTO users (telegram_user_id, chat_id, created_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (123, 456, now, now),
    )
    test_store.conn.commit()

    bot.asyncio.run(bot.begin_post_forum_plan_flow(None, test_store.get_user(123), source_selector="0"))

    assert len(replies) == 1
    assert "Личный план действий по разбору" in replies[0]
    assert "Вопрос 1/2" not in replies[0]
    assert "Текущий ответ" not in replies[0]


def test_personal_plan_flow_uses_one_freeform_question():
    user = {"active_flow": "post_forum_plan"}

    assert [question.key for question in bot.flow_questions_for_user(user)] == [bot.PERSONAL_PLAN_KEY]


def test_flow_next_can_skip_unanswered_question(tmp_path, monkeypatch):
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
    assert user["active_step"] == 1
    assert "Вопрос 2/" in replies[-1]


def test_menu_text_interrupts_active_flow(tmp_path, monkeypatch):
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
            state, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (123, 456, "update", 3, '{"answers": {"q": "old"}}', "flow:await_next", now, now),
    )
    test_store.conn.commit()

    bot.asyncio.run(bot.route_text(None, None, test_store.get_user(123), "Апдейты"))

    user = test_store.get_user(123)
    assert user["active_flow"] is None
    assert user["active_step"] == 0
    assert user["flow_payload"] == "{}"
    assert user["state"] is None
    assert any("Апдейты" in reply for reply in replies)


def test_new_update_requires_settings_when_profile_incomplete(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    replies = []
    started = []

    async def fake_reply(_update, text, **_kwargs):
        replies.append(text)

    async def fake_start_onboarding(_update, _context, user):
        started.append(user["telegram_user_id"])

    monkeypatch.setattr(bot, "reply", fake_reply)
    monkeypatch.setattr(bot, "start_onboarding", fake_start_onboarding)
    now = bot.now_iso()
    test_store.conn.execute(
        """
        INSERT INTO users (
            telegram_user_id, chat_id, full_name, business_club, forum_group,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (123, 456, "Андрей Путин", "Другое", "Форум", now, now),
    )
    test_store.conn.commit()

    bot.asyncio.run(bot.run_menu_action("updates.new", None, None, test_store.get_user(123)))

    assert started == [123]
    assert "Перед апдейтом нужно заполнить блок настроек" in replies[0]


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


def test_replace_personal_plan_section_appends_last_section():
    markdown = "# Апдейт\n\n## Я\n\n**Вопрос**\n\nОтвет\n"
    updated = bot.replace_personal_plan_section(
        markdown,
        {
            "meeting_gratitude": "Поблагодарил себя за честность.",
            "next_actions": "Согласовал встречу в офисе во вторник.",
        },
    )

    assert updated.rstrip().endswith("Согласовал встречу в офисе во вторник.")
    assert "## Личный план действий" in updated
    assert "Поблагодарил себя за честность." in updated


def test_replace_personal_plan_section_removes_empty_existing_section():
    markdown = "# Апдейт\n\n## Я\n\n**Вопрос**\n\nОтвет\n\n## Личный план действий\n\n**План**\n\nСтарый план\n"
    updated = bot.replace_personal_plan_section(markdown, {})

    assert "## Я" in updated
    assert "Старый план" not in updated
    assert "## Личный план действий" not in updated


def test_strip_empty_personal_plan_section_from_html_source():
    markdown = (
        "# Апдейт\n\n"
        "## Личный план действий\n\n"
        "**Благодарность себе и другим: что важно не забыть проговорить?**\n\n"
        "_Нет ответа_\n\n"
        "## Я\n\n"
        "**Вопрос**\n\nОтвет\n"
    )
    html = bot.markdown_to_readable_html(markdown)

    assert "<h2>Личный план действий</h2>" not in html
    assert "<h2>Я</h2>" in html


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

    assert [button.text for button in buttons] == ["Апдейты"]
    assert [button.callback_data for button in buttons] == ["updates:menu"]


def test_main_menu_uses_high_level_submenus():
    main_buttons = [button.text for row in bot.MAIN_KEYBOARD.keyboard for button in row]
    inline_callbacks = [
        button.callback_data
        for row in bot.main_inline_keyboard().inline_keyboard
        for button in row
        if button.callback_data
    ]

    assert main_buttons == ["Апдейты", "Моя форум-группа", "Дневник", "Личный кабинет", "Доп. информация"]
    assert inline_callbacks == ["updates:menu", "forum_group:menu", "diary:menu", "profile:show", "menu:info"]
    assert "Удалить мои данные" not in main_buttons


def test_information_submenu_contains_info_actions():
    labels = [button.text for row in bot.info_inline_keyboard().inline_keyboard for button in row]

    assert "О боте" in labels
    assert "Ищу психолога" in labels
    assert "Ищу ментора" in labels
    assert "Связаться с автором" not in labels
    assert "Сделать собственный бот" not in labels
    assert "Назад в меню" in labels


def test_update_file_captions_are_reader_facing():
    assert bot.UPDATE_MD_CAPTION == "Форумный апдейт в .md для ИИ."
    assert bot.UPDATE_HTML_CAPTION == "Форумный апдейт в .html для чтения на форуме."
    assert "кодиров" not in bot.UPDATE_MD_CAPTION.casefold()
    assert "iphone" not in bot.UPDATE_MD_CAPTION.casefold()


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
    updates_labels = [
        button.text for row in bot.updates_menu_keyboard({"last_update_at": "2026-04-26T10:00:00+03:00"}).inline_keyboard for button in row
    ]
    forum_group_labels = [button.text for row in bot.forum_group_menu_keyboard().inline_keyboard for button in row]
    diary_labels = [button.text for row in bot.diary_menu_keyboard({"diary_enabled": 0}).inline_keyboard for button in row]
    guide_labels = [button.text for row in bot.guide_keyboard().inline_keyboard for button in row]
    edit_labels = [
        button.text
        for row in bot.business_club_keyboard(prefix="profile:club").inline_keyboard
        for button in row
    ]

    assert "Назад" in profile_labels
    assert "Назад" in updates_labels
    assert "Назад" in forum_group_labels
    assert "Назад" in diary_labels
    assert "Назад" in guide_labels
    assert "Назад" in edit_labels
    assert forum_group_labels[:2] == ["Дата следующего форума", "Здоровье форум-группы"]


def test_flow_keyboard_uses_native_next_and_back_actions():
    keyboard = bot.flow_keyboard().inline_keyboard
    labels = [button.text for row in keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard for button in row]

    assert labels == ["⬅️", "➡️"]
    assert callbacks == ["flow:back", "flow:next"]
    assert "Отменить сценарий" not in labels


def test_update_start_keyboard_offers_new_or_edit():
    labels = [button.text for row in bot.update_start_keyboard().inline_keyboard for button in row]

    assert labels == ["Новый апдейт", "Изменить предыдущий"]


def test_updates_menu_is_simple_and_profile_has_delete():
    update_labels = [
        button.text
        for row in bot.updates_menu_keyboard({"last_update_at": "2026-04-26T10:00:00+03:00"}).inline_keyboard
        for button in row
    ]
    profile_labels = [button.text for row in bot.profile_cabinet_keyboard().inline_keyboard for button in row]

    assert update_labels == ["Новый апдейт", "Мои апдейты", "Назад"]
    assert "Удалить мои данные" in profile_labels
    assert "Скачать апдейт" not in profile_labels
    assert "Доп. информация" not in profile_labels


def test_updates_list_keyboard_has_only_dynamics_and_back(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "UPDATES_DIR", tmp_path)
    user = {"telegram_user_id": 123}
    user_dir = tmp_path / "123"
    user_dir.mkdir()
    (user_dir / "forum-update-20260426-1000.md").write_text("# Апдейт", encoding="utf-8")

    buttons = [
        button
        for row in bot.updates_list_keyboard(user).inline_keyboard
        for button in row
    ]
    labels = [button.text for button in buttons]
    callbacks = [button.callback_data for button in buttons]

    assert labels == ["Динамика апдейтов", "Назад"]
    assert callbacks == ["updates:chat", "updates:menu"]


def test_update_item_line_uses_deeplinks_for_actions():
    line = bot.update_item_line(
        1,
        {"selector": "0", "filename": "forum-update.md", "date": "31.05.2026"},
    )

    assert line.startswith("<b>31.05.2026</b>\n - Скачать:")
    assert "forum-update.md" not in line
    assert 'href="https://t.me/ForumUpdateHelperBot?start=upd_md_0"' in line
    assert 'href="https://t.me/ForumUpdateHelperBot?start=upd_html_0"' in line
    assert 'href="https://t.me/ForumUpdateHelperBot?start=upd_edit_0"' in line
    assert 'href="https://t.me/ForumUpdateHelperBot?start=upd_plan_0"' in line
    assert "Скачать:" in line
    assert "[.md]" in line
    assert "(полная, для ИИ)" in line
    assert "[.html]" in line
    assert "(короткая, для чтения)" in line
    assert "Ввести личный план действий по разбору" in line
    assert "Редактировать" in line


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


def test_markdown_download_bytes_include_utf8_bom_once():
    payload = bot.markdown_bytes_for_download("\ufeff# Форум")

    assert payload.startswith(bot.UTF8_BOM)
    assert payload.count(bot.UTF8_BOM) == 1
    assert payload.decode("utf-8-sig") == "# Форум"


def test_markdown_file_migration_adds_bom_and_repairs_mojibake(tmp_path):
    path = tmp_path / "update.md"
    path.write_text("# Ð¤Ð¾Ñ€ÑƒÐ¼", encoding="utf-8")

    assert bot.ensure_markdown_file_has_utf8_bom(path) is True
    assert path.read_bytes().startswith(bot.UTF8_BOM)
    assert bot.read_markdown_file_text(path) == "# Форум"


def test_markdown_to_readable_html_has_charset_and_formatting():
    html = bot.markdown_to_readable_html("# Форум\n\n- Создано: 2026-05-31 14:20\n\n## Раздел\n\n**Вопрос**\n\n- пункт", title="Апдейт")

    assert '<meta charset="utf-8">' in html
    assert "<h1>Форум</h1>" in html
    assert "Короткая версия для чтения на форуме" in html
    assert "<strong>Дата заполнения:</strong> 2026-05-31 14:20" in html
    assert "<h2>Раздел</h2>" in html
    assert "<h3>Вопрос</h3>" not in html
    assert "<li><strong>Вопрос: пункт</strong></li>" in html


def test_extract_rating_summary_compacts_current_and_previous_month():
    assert bot.extract_rating_summary("Оценка этого месяца: 8/10. Оценка предыдущего месяца: 6/10.") == "6->8/10"
    assert bot.extract_rating_summary("6->8/10, стало спокойнее") == "6->8/10"
    assert bot.extract_rating_summary("8/10. Стало спокойнее.") == "8/10"


def test_markdown_to_readable_html_keeps_spheres_and_short_question_labels():
    markdown = (
        "# Форум-апдейт — High Level\n\n"
        "- Участник: Андрей\n"
        "- Методика: С личной стратегией (X-Competence)\n"
        "- Дата форума: 06.06.2026\n"
        "- Создано: 2026-05-31 14:20\n\n"
        "## Часть 1. Оценка трёх сфер\n\n"
        "**Моё дело: дай оценку месяца:\n"
        "- оценку этого месяца;\n"
        "- оценку предыдущего месяца;\n"
        "- что изменилось.**\n\n"
        "Оценка этого месяца: 8/10. Оценка предыдущего месяца: 6/10. Стало спокойнее.\n\n"
        "- Запустил новый процесс\n"
        "- Договорился с партнёром\n\n"
        "**Я: собери ретроспективу периода.\n\n"
        "- Что ты планировал на прошедший период?\n"
        "- Что получил по факту?**\n\n"
        "Планировал восстановиться. Получил больше энергии."
    )

    html = bot.markdown_to_readable_html(markdown)

    assert "<h2>Я</h2>" in html
    assert "<h2>Моё дело</h2>" in html
    assert "<h3>Оценка месяца</h3>" not in html
    assert "<li><strong>Оценка месяца: 6-&gt;8/10</strong></li>" in html
    assert "<li><strong>Оценка месяца: Стало спокойнее.</strong></li>" in html
    assert "<li><strong>Оценка месяца: Запустил новый процесс</strong></li>" in html
    assert "<li><strong>Оценка месяца: Договорился с партнёром</strong></li>" in html
    assert "<li><strong>Как было / что получилось: Планировал восстановиться. Получил больше энергии.</strong></li>" in html
    assert "Дата форума" in html
    assert "2026-05-31 14:20" in html
    assert "**Моё дело" not in html


def test_ai_brief_data_to_html_body_renders_bold_theses():
    _title, body = bot.ai_brief_data_to_html_body(
        {
            "title": "Форум-апдейт",
            "meta": [{"label": "Дата заполнения", "value": "31.05.2026"}],
            "sections": [
                {
                    "title": "Я",
                    "bullets": [
                        {"label": "Оценка месяца", "text": "6->8/10"},
                        {"label": "Чувства", "text": "спокойствие, интерес"},
                    ],
                }
            ],
        },
        "Форум-апдейт",
    )

    html = "".join(body)
    assert "<h2>Я</h2>" in html
    assert "<li><strong>Оценка месяца: 6-&gt;8/10</strong></li>" in html
    assert "<li><strong>Чувства: спокойствие, интерес</strong></li>" in html


def test_markdown_to_ai_readable_html_uses_structured_ai_response(monkeypatch):
    class FakeResponses:
        def create(self, **_kwargs):
            class Response:
                output_text = bot.json.dumps(
                    {
                        "title": "ИИ-апдейт",
                        "meta": [{"label": "Дата заполнения", "value": "31.05.2026"}],
                        "sections": [
                            {
                                "title": "Моё дело",
                                "bullets": [{"label": "Оценка месяца", "text": "6->8/10"}],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

            return Response()

    class FakeOpenAI:
        responses = FakeResponses()

    monkeypatch.setattr(bot, "_openai", FakeOpenAI())

    html = bot.asyncio.run(bot.markdown_to_ai_readable_html("# Фолбэк\n\n## Раздел\n\n**Вопрос**\n\nОтвет"))

    assert "<h1>ИИ-апдейт</h1>" in html
    assert "<h2>Моё дело</h2>" in html
    assert "<li><strong>Оценка месяца: 6-&gt;8/10</strong></li>" in html


def test_readable_html_cache_key_changes_when_markdown_changes():
    first = bot.readable_html_cache_key("# Апдейт\n\nОтвет")
    second = bot.readable_html_cache_key("# Апдейт\n\nОтвет изменён")

    assert first != second
    assert bot.readable_html_filename("forum-update.md", first).startswith("forum-update-read-")


def test_send_readable_update_file_uses_cache(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    calls = {"ai": 0}
    sent_files = []
    replies = []

    async def fake_ai(markdown, title="Форумный апдейт"):
        calls["ai"] += 1
        return f"<html><body>{bot.esc(title)} {bot.esc(markdown)}</body></html>", True

    async def fake_reply(_update, text, **_kwargs):
        replies.append(text)

    async def fake_send_temp_document(_update, payload, *, filename, suffix, caption):
        sent_files.append((payload.decode("utf-8"), filename, suffix, caption))

    async def fake_send_chat_action(_update, _action):
        return None

    monkeypatch.setattr(bot, "markdown_to_ai_readable_html_result", fake_ai)
    monkeypatch.setattr(bot, "reply", fake_reply)
    monkeypatch.setattr(bot, "send_temp_document", fake_send_temp_document)
    monkeypatch.setattr(bot, "send_chat_action", fake_send_chat_action)

    user = {
        "telegram_user_id": 123,
        "last_update_markdown": "# Апдейт\n\n## Я\n\n**Вопрос**\n\nОтвет",
        "last_update_filename": "forum-update-20260531-1200.md",
    }

    bot.asyncio.run(bot.send_readable_update_file(None, user, source_selector="latest"))
    bot.asyncio.run(bot.send_readable_update_file(None, user, source_selector="latest"))

    assert calls["ai"] == 1
    assert len(sent_files) == 2
    assert sent_files[0][1] == sent_files[1][1]
    assert len(replies) == 1


def test_send_readable_update_file_does_not_cache_uncacheable_fallback(tmp_path, monkeypatch):
    test_store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", test_store)
    calls = {"html": 0}
    replies = []

    async def fake_html(_markdown, title="Форумный апдейт"):
        calls["html"] += 1
        return f"<html><body>{bot.esc(title)}</body></html>", False

    async def fake_reply(_update, text, **_kwargs):
        replies.append(text)

    async def fake_send_temp_document(*_args, **_kwargs):
        return None

    async def fake_send_chat_action(_update, _action):
        return None

    monkeypatch.setattr(bot, "markdown_to_ai_readable_html_result", fake_html)
    monkeypatch.setattr(bot, "reply", fake_reply)
    monkeypatch.setattr(bot, "send_temp_document", fake_send_temp_document)
    monkeypatch.setattr(bot, "send_chat_action", fake_send_chat_action)

    user = {
        "telegram_user_id": 123,
        "last_update_markdown": "# Апдейт\n\nОтвет",
        "last_update_filename": "forum-update.md",
    }

    bot.asyncio.run(bot.send_readable_update_file(None, user, source_selector="latest"))
    bot.asyncio.run(bot.send_readable_update_file(None, user, source_selector="latest"))

    assert calls["html"] == 2
    assert len(replies) == 2


def test_forum_guide_context_loads_materials():
    context = bot.load_forum_guide_context("Классическая (YPO)")

    assert "Классическая методика" in context
    assert "Форум — это" in context
    assert "Источник:" not in context


def test_forum_guide_context_loads_x_competence_materials():
    context = bot.load_forum_guide_context("С личной стратегией (X-Competence)")

    assert "Методика с личной стратегией" in context
    assert "Классическая методика" not in context
    assert "Источник:" not in context


def test_ai_forum_standard_markdown_contains_agent_instruction():
    text = bot.build_ai_forum_standard_markdown({"methodology": "С личной стратегией (X-Competence)"})

    assert "# Стандарт форума и инструкция для ИИ-агента" in text
    assert "## Инструкция для ИИ-агента" in text
    assert "Классическая методика (YPO)" in text
    assert "С личной стратегией (X-Competence)" in text
    assert "Методика с личной стратегией" in text
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
    assert "last_update_answers" in columns
    assert "last_update_markdown" in columns
    assert "last_post_forum_plan_answers" in columns
    tables = {
        row["name"]
        for row in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    }
    assert "readable_html_cache" in tables


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
