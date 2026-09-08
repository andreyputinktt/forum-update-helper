import asyncio
import json
from types import SimpleNamespace

import pytest

from test_update_history import bot, history
from transcription_vocabulary import select_terms


@pytest.fixture
def vocabulary(history, monkeypatch):
    store, root = history
    path = root / "lexicon.json"
    path.write_text(json.dumps({"terms": [
        {"term": "Джеклин", "weight": 2.2, "source_category": "peoples", "last_seen": "/private/source"},
        {"term": "Circulera", "aliases": ["Циркулера"], "weight": 12},
        {"term": "ПроектА", "weight": 1},
    ]}, ensure_ascii=False))
    monkeypatch.setattr(bot, "TRANSCRIPTION_LEXICON_USER_ID", 123)
    monkeypatch.setattr(bot, "TRANSCRIPTION_LEXICON_PATH", str(path))
    monkeypatch.setattr(bot, "TRANSCRIPTION_PRIORITY_TERMS", ("Джеклин",))
    return store, path


def test_priority_context_and_budget(vocabulary):
    _, path = vocabulary
    before = path.read_bytes()
    assert select_terms(path, "Обсуждали ПроектА", ("Джеклин",), limit=2) == ["Джеклин", "ПроектА"]
    assert select_terms(path, "Циркулера", limit=1) == ["Circulera"]
    assert sum(len(term) + 2 for term in select_terms(path, "", char_budget=12)) <= 12
    assert path.read_bytes() == before


def test_only_owner_receives_private_terms(vocabulary):
    store, _ = vocabulary
    owner = bot.transcription_prompt(store.get_user(123))
    other = bot.transcription_prompt(store.get_user(456))  # Same username, different immutable ID.
    assert "Джеклин" in owner and "Джеклин" not in other
    assert "/private/source" not in owner
    assert "aliases" not in owner
    assert bot.transcription_prompt() == other


def test_missing_invalid_and_updated_dictionary(vocabulary):
    store, path = vocabulary
    base = bot.transcription_prompt()
    path.unlink()
    assert bot.transcription_prompt(store.get_user(123)) == base
    for invalid in ("not json", "null", '{"terms":null}', '{"terms":[null,{},42]}'):
        path.write_text(invalid)
        assert bot.transcription_prompt(store.get_user(123)) == base
    path.write_text('[{"term":"НовоеИмя"}]')
    assert "НовоеИмя" in bot.transcription_prompt(store.get_user(123))


def test_current_and_previous_answers_are_selection_context_only(vocabulary, monkeypatch):
    store, _ = vocabulary
    captured = []

    def select(path, context, priority):
        captured.append(context)
        return ["Джеклин"]

    monkeypatch.setattr(bot, "select_terms", select)
    user = store.update_user(123, methodology=bot.METHODOLOGY_STRATEGY)
    user = store.set_flow(123, "update", 3, {
        "interview_version": 2,
        "answers": {"classic_Бизнес_plus": "Частное текущее событие"},
        "previous_update": {"answers": {"retrospective_Моё дело": "Частное прошлое событие", "next_period_Моё дело": "Частный план"}},
    })
    prompt = bot.transcription_prompt(user)
    assert all(part in captured[0] for part in ("Частное текущее событие", "Частное прошлое событие", "Частный план"))
    assert "Частное" not in prompt and "Частный" not in prompt


def test_asr_receives_hints_and_does_not_replace_joker(vocabulary, monkeypatch, tmp_path):
    store, _ = vocabulary
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="Вчера посмотрел фильм Джокер.")

    monkeypatch.setattr(bot, "_openai", SimpleNamespace(audio=SimpleNamespace(transcriptions=SimpleNamespace(create=create))))
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"test audio")
    result = asyncio.run(bot.transcribe_audio(audio, store.get_user(123)))
    assert "Джеклин" in calls[0]["prompt"]
    assert result == "Вчера посмотрел фильм Джокер."
    assert calls[0]["language"] == bot.TRANSCRIBE_LANGUAGE


def test_audio_handler_passes_owner_and_deletes_audio(vocabulary, monkeypatch):
    from pathlib import Path
    store, _ = vocabulary
    seen = []

    async def noop(*args, **kwargs):
        pass

    async def download(custom_path):
        Path(custom_path).write_bytes(b"audio")

    async def get_file():
        return SimpleNamespace(download_to_drive=download)

    async def transcribe(path, user):
        seen.append((path, user["telegram_user_id"]))
        return "Джеклин"

    monkeypatch.setattr(bot, "_openai", object())
    monkeypatch.setattr(store, "ensure_user", lambda update: store.get_user(123))
    monkeypatch.setattr(bot, "transcribe_audio", transcribe)
    monkeypatch.setattr(bot, "reply", noop)
    monkeypatch.setattr(bot, "route_text", noop)
    message = SimpleNamespace(chat=SimpleNamespace(send_action=noop), voice=SimpleNamespace(get_file=get_file), audio=None)
    asyncio.run(bot.handle_voice_or_audio(SimpleNamespace(effective_message=message), None))
    assert seen[0][1] == 123
    assert not seen[0][0].exists()
