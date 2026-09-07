import asyncio
import json
from types import SimpleNamespace

import pytest

from test_update_history import bot, history


@pytest.fixture
def interview(history, monkeypatch):
    store, root = history
    store.update_user(123, forum_group="Мой форум", methodology=bot.METHODOLOGY_STRATEGY, next_forum_date="2026-10-01")
    replies = []

    async def reply(_update, text, **kwargs):
        replies.append((text, kwargs.get("reply_markup")))

    monkeypatch.setattr(bot, "reply", reply)
    return store, root, replies


def test_unified_questionnaire_covers_both_formats_once():
    questions = bot.UNIFIED_UPDATE_QUESTIONS
    keys = [q.key for q in questions]
    assert len(questions) == len(set(keys)) == 18
    assert all(key in keys for key in bot.event_keys())
    assert len([key for key in keys if key.startswith("rating_")]) == 3
    assert keys[-3:] == ["main_request_core", "main_request_details", "main_request_help"]
    assert all("Чувства:" in q.prompt and "Важность:" in q.prompt for q in questions if q.key in bot.event_keys())


def test_previous_answers_are_frozen_group_scoped_and_not_prefilled(interview):
    store, _, replies = interview
    user = store.get_user(123)
    old_answers = {"rating_Моё дело": "6/10 <было>", "next_period_Моё дело": "Сделать важный шаг"}
    user = bot.save_completed_update(user, bot.build_update_markdown(user, old_answers, questions=bot.UPDATE_QUESTIONS))
    foreign_group = {**user, "forum_group": "Другой форум"}
    user = bot.save_completed_update(user, bot.build_update_markdown(foreign_group, {"rating_Моё дело": "Чужой контекст"}))
    asyncio.run(bot.begin_update_flow(None, user))
    user = store.get_user(123)
    payload = store.payload(user)
    assert payload["answers"] == {}
    assert payload["previous_update"]["answers"]["rating_Моё дело"] == "6/10 <было>"
    assert "&lt;было&gt;" in replies[-1][0]
    assert "Чужой контекст" not in replies[-1][0]
    bot.save_completed_update(user, bot.build_update_markdown(user, {"rating_Моё дело": "9/10"}))
    asyncio.run(bot.ask_current_question(None, store.get_user(123), bot.UNIFIED_UPDATE_QUESTIONS))
    assert "6/10 &lt;было&gt;" in replies[-1][0]
    hint = bot.previous_question_hint(next(q for q in bot.UNIFIED_UPDATE_QUESTIONS if q.key == "retrospective_Моё дело"), payload)
    assert "Сделать важный шаг" in hint


def test_reuse_clear_and_stale_question_buttons(interview):
    store, _, _ = interview
    payload = {"interview_version": 2, "answers": {}, "previous_update": {"answers": {"rating_Моё дело": "6/10"}, "date": "01.09.2026"}}
    user = store.set_flow(123, "update", 0, payload)
    asyncio.run(bot.change_question_answer(None, None, user, "flow:reuse:1"))
    assert store.payload(store.get_user(123))["answers"] == {}
    asyncio.run(bot.change_question_answer(None, None, user, "flow:reuse:0"))
    assert store.payload(store.get_user(123))["answers"]["rating_Моё дело"] == "6/10"
    asyncio.run(bot.change_question_answer(None, None, store.get_user(123), "flow:clear:0"))
    assert store.payload(store.get_user(123))["answers"] == {}


def test_legacy_draft_keeps_order_then_collects_missing_events(interview):
    store, _, _ = interview
    user = store.set_flow(123, "update", 8, {"answers": {"main_request_money": "100 рублей"}})
    assert bot.update_questions_for_user(user) == bot.UPDATE_QUESTIONS
    asyncio.run(bot.finish_flow(None, None, user))
    user = store.get_user(123)
    assert user["active_flow"] == "update_extra"
    assert len(bot.flow_questions_for_user(user)) == 6
    assert store.payload(user)["answers"]["main_request_money"] == "100 рублей"
    assert user["last_update_filename"] is None


def test_mentor_rounds_persist_and_only_final_save_exports(interview):
    store, root, _ = interview
    answers = {"rating_Моё дело": "8/10", "main_request_core": "Как мне доверять?", "classic_Бизнес_plus": "Событие: Запустил проект\nВажность: Давно хотел\nЧувства: Гордость"}
    user = store.set_flow(123, "update", 18, {"interview_version": 2, "answers": answers})
    asyncio.run(bot.finish_flow(None, None, user))
    user = store.get_user(123)
    assert user["active_flow"] == "update_mentor"
    assert user["last_update_filename"] is None
    assert not (root / "profile").exists()
    asyncio.run(bot.record_mentor_answer(None, user, "Мне было тревожно"))
    reopened = bot.Store(store.db_path)
    try:
        assert reopened.payload(reopened.get_user(123))["mentor_dialogue"][0]["answer"] == "Мне было тревожно"
    finally:
        reopened.conn.close()
    asyncio.run(bot.handle_mentor_action(None, None, store.get_user(123), "mentor:next:0"))
    # A double click on an old button must not skip the new question.
    asyncio.run(bot.handle_mentor_action(None, None, store.get_user(123), "mentor:next:0"))
    assert len(store.payload(store.get_user(123))["mentor_dialogue"]) == 2
    asyncio.run(bot.handle_mentor_action(None, None, store.get_user(123), "mentor:next:1"))
    asyncio.run(bot.handle_mentor_action(None, None, store.get_user(123), "mentor:next:2"))
    user = store.get_user(123)
    assert user["active_flow"] is None
    markdown = user["last_update_markdown"]
    assert "Традиционный форум" in markdown and "X-Competence" in markdown
    assert "Уточнения с ментором" in markdown and "Мне было тревожно" in markdown
    assert len(list((root / "profile").glob("forum-update-*.md"))) == 1
    parsed = bot.parse_update_markdown_answers(markdown, bot.UNIFIED_UPDATE_QUESTIONS)
    assert parsed["classic_Бизнес_plus"] == answers["classic_Бизнес_plus"]
    html = asyncio.run(bot.markdown_to_ai_readable_html_result(markdown))[0]
    assert "Традиционный форум" in html and "X-Competence — Моё дело" in html and "Мне было тревожно" in html


def test_mentor_can_save_immediately(interview):
    store, _, _ = interview
    user = store.set_flow(123, "update", 18, {"interview_version": 2, "answers": {"main_request_core": "Важный вопрос"}})
    asyncio.run(bot.finish_flow(None, None, user))
    asyncio.run(bot.handle_mentor_action(None, None, store.get_user(123), "mentor:save"))
    assert store.get_user(123)["active_flow"] is None
    assert "Важный вопрос" in store.get_user(123)["last_update_markdown"]


def test_mentor_uses_actual_previous_reply_and_falls_back(interview, monkeypatch):
    store, _, _ = interview
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(output_text="Что в этом событии важнее всего для тебя?")

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(bot, "_openai", SimpleNamespace(with_options=lambda **kwargs: client))
    payload = {"answers": {"main_request_core": "Запрос"}, "mentor_dialogue": [{"question": "Что случилось?", "answer": "Сорвал встречу и стыжусь"}]}
    result = asyncio.run(bot.generate_mentor_question(store.get_user(123), payload))
    assert result.endswith("?")
    assert "Сорвал встречу и стыжусь" in calls[-1]["input"]
    assert json.loads(calls[-1]["input"])["round"] == 2

    def timeout(**kwargs):
        raise TimeoutError("provider unavailable")

    client.responses.create = timeout
    assert "потребность" in asyncio.run(bot.generate_mentor_question(store.get_user(123), payload))


def test_structuring_rejects_invented_feelings(interview, monkeypatch):
    def create(**kwargs):
        return SimpleNamespace(output_text=json.dumps({"classic_Бизнес_plus": {"event": "Открыл офис", "importance": "Для команды", "feelings": "Страх провала"}}, ensure_ascii=False))

    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(bot, "_openai", SimpleNamespace(with_options=lambda **kwargs: client))
    fields = asyncio.run(bot.structure_event_answers({"classic_Бизнес_plus": "Открыл офис. Для команды."}))
    assert fields["classic_Бизнес_plus"]["event"] == "Открыл офис"
    assert fields["classic_Бизнес_plus"]["feelings"] == ""
    rendered = bot.traditional_markdown({"classic_Бизнес_plus": "Открыл офис. Для команды."}, fields)
    assert "Страх провала" not in rendered
    assert "Открыл офис. Для команды." in rendered
