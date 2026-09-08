import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from test_update_history import bot, history
from repair_update import apply, checksum


def sample():
    answers = {"rating_Моё дело": "6/10", "classic_Бизнес_plus": "Лиды для приоснова. Не приоснова, а pay.example.com",
               "next_period_Моя семья / близкие": "Помириться с Джокером. Не Джокер, а Джеклин",
               "rating_Я": "7/11", "main_request_core": "Как мне понять ситуацию?"}
    dialogue = [{"question": "Что проверить?", "answer": "Что я упускаю?"}]
    def field(text, key, quote=None):
        source = bot.editorial.source_material(answers, dialogue)
        return {"text": text, "evidence": [{"source": key, "quote": quote or source[key]}]}
    data = {"spheres": [{"name": classic, **{key: None for key in bot.editorial.FIELDS}} for _, classic in bot.SPHERE_PAIRS],
            "request": {"question": field("Что я упускаю?", "mentor:0"), "context": None, "experience": None},
            "uncertainties": [field("Оценка личного записана как 7/11; нужно уточнить шкалу.", "rating_Я")]}
    data["spheres"][0]["rating"] = field("6/10", "rating_Моё дело")
    data["spheres"][0]["positive_event"] = field("Появились лиды для pay.example.com.", "classic_Бизнес_plus")
    data["spheres"][1]["next_period"] = field("Хочу помириться с Джеклин.", "next_period_Моя семья / близкие")
    return answers, dialogue, data


def test_corrected_reading_view_preserves_sources_and_history_answers():
    answers, dialogue, data = sample()
    bot.editorial.validate_editorial(data, bot.editorial.source_material(answers, dialogue))
    md = bot.editorial.render_markdown("# Форум-апдейт — Тест", data, answers, dialogue)
    visible = bot.editorial.visible_markdown(md)
    html, cached = asyncio.run(bot.markdown_to_ai_readable_html_result(md))
    assert cached and "Джеклин" in html and "pay.example.com" in html
    assert "Джокер" not in html and "приоснова" not in html and "_Не уточнено_" not in html
    assert "7/11" in html and "7/10" not in html
    assert visible.count("Появились лиды") == 1
    assert bot.editorial.read_source(md)["answers"] == answers
    parsed = bot.parse_update_markdown_answers(md, bot.UNIFIED_UPDATE_QUESTIONS)
    assert parsed["next_period_Моя семья / близкие"] == "Хочу помириться с Джеклин."
    plan = bot.replace_personal_plan_section(md, {bot.PERSONAL_PLAN_KEY: "Обсудить ситуацию"})
    assert bot.parse_personal_plan_answers(plan)[bot.PERSONAL_PLAN_KEY] == "Обсудить ситуацию"
    assert "Обсудить ситуацию" in bot.markdown_to_readable_html(plan)


def test_no_silent_html_truncation_or_source_comment_injection():
    answers, dialogue, data = sample()
    answers["raw"] = "--> <script>alert(1)</script>"
    long_text = "Существенный факт. " * 55 + "Последнее предложение."
    data["spheres"][0]["overview"] = {"text": long_text + " <script>alert(2)</script>"}
    md = bot.editorial.render_markdown("# Апдейт", data, answers, dialogue)
    html = bot.markdown_to_readable_html(md)
    assert "Последнее предложение." in html and "<script>" not in html
    assert "alert(1)" not in html
    assert bot.editorial.read_source(md)["answers"] == answers


@pytest.mark.parametrize("failure", ["quote", "rating", "missing", "empty"])
def test_rejects_unsupported_or_incomplete_output(failure):
    answers, dialogue, data = sample()
    if failure == "quote":
        data["spheres"][0]["positive_feelings"] = {"text": "Я в восторге", "evidence": [{"source": "classic_Бизнес_plus", "quote": "восторге"}]}
    elif failure == "rating":
        data["spheres"][2]["rating"] = {"text": "7/10", "evidence": [{"source": "rating_Я", "quote": "7/11"}]}
    elif failure == "missing":
        del data["spheres"][1]["overview"]
    else:
        data["spheres"] = []
    with pytest.raises(ValueError):
        bot.editorial.validate_editorial(data, bot.editorial.source_material(answers, dialogue))


def test_editor_provider_uses_full_sources_and_rejects_truncation(history, monkeypatch):
    store, _ = history
    answers, dialogue, data = sample()
    user = {**store.get_user(123), "flow_payload": json.dumps({"mentor_dialogue": dialogue})}
    calls = []
    response = SimpleNamespace(status="completed", output_text=json.dumps(data))
    def create(**kwargs):
        calls.append(kwargs)
        return response
    client = SimpleNamespace(responses=SimpleNamespace(create=create))
    monkeypatch.setattr(bot, "_openai", SimpleNamespace(with_options=lambda **kwargs: client))
    md = asyncio.run(bot.compose_edited_update(user, answers))
    assert "Хочу помириться с Джеклин" in bot.editorial.visible_markdown(md)
    material = json.loads(calls[-1]["input"])
    assert material["answers"] == answers and material["mentor"] == dialogue
    assert "spelling_context" not in material
    response.status = "incomplete"
    with pytest.raises(ValueError, match="Incomplete"):
        asyncio.run(bot.compose_edited_update(user, answers))


def test_editing_failure_keeps_draft_and_does_not_export(history, monkeypatch):
    store, root = history
    async def fail(*args, **kwargs):
        raise TimeoutError()
    async def reply(*args, **kwargs):
        pass
    monkeypatch.setattr(bot, "compose_edited_update", fail)
    monkeypatch.setattr(bot, "reply", reply)
    store.update_user(123, methodology=bot.METHODOLOGY_STRATEGY)
    payload = {"answers": {"main_request_core": "Запрос"}, "interview_version": 2, "mentor_dialogue": []}
    user = store.set_flow(123, "update_mentor", 0, payload)
    asyncio.run(bot.handle_mentor_action(None, None, user, "mentor:save"))
    user = store.get_user(123)
    assert user["active_flow"] == "update_mentor" and store.payload(user) == payload
    assert user["last_update_filename"] is None and not (root / "profile").exists()


def test_repair_rejects_changed_source(history, monkeypatch):
    store, root = history
    monkeypatch.setattr(bot, "DATA_DIR", root)
    user = bot.save_completed_update(store.get_user(123), "# original")
    filename = user["last_update_filename"]
    folder = root / "repairs" / "123" / filename.removesuffix(".md")
    folder.mkdir(parents=True)
    answers, dialogue, data = sample()
    md = bot.editorial.render_markdown("# Апдейт", data, answers, dialogue)
    (folder / "candidate.md").write_text(md)
    (folder / "manifest.json").write_text(json.dumps({"filename": filename, "user_id": 123,
        "source_sha256": "stale", "candidate_sha256": checksum(md)}))
    with pytest.raises(ValueError, match="Source changed"):
        apply(123, filename)
    assert store.get_user(123)["last_update_markdown"] == "# original"
