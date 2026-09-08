import asyncio
import importlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("DATA_DIR", "/tmp/forum-update-helper-tests")
bot = importlib.import_module("main")


@pytest.fixture
def history(tmp_path, monkeypatch):
    store = bot.Store(tmp_path / "state.sqlite3")
    monkeypatch.setattr(bot, "store", store)
    monkeypatch.setattr(bot, "UPDATES_DIR", tmp_path / "updates")
    monkeypatch.setattr(bot, "UPLOADS_DIR", tmp_path / "uploads")
    monkeypatch.setattr(bot, "PROFILE_EXPORT_USER_ID", 123)
    monkeypatch.setattr(bot, "PROFILE_EXPORT_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(bot, "_openai", None)
    for user_id in (123, 456):
        store.conn.execute(
            "INSERT INTO users (telegram_user_id,chat_id,username,keep_files,created_at,updated_at) VALUES (?,?,?,?,?,?)",
            (user_id, user_id, "utandr", 1, bot.now_iso(), bot.now_iso()),
        )
    store.conn.commit()
    yield store, tmp_path
    store.conn.close()


def test_two_completions_keep_old_link_and_distinct_files(history):
    store, root = history
    first = bot.save_completed_update(store.get_user(123), "# first")
    item = bot.stored_update_items(first)[0]
    second = bot.save_completed_update(first, "# second")
    assert first["last_update_filename"] != second["last_update_filename"]
    assert len(bot.saved_update_files(second)) == 2
    assert bot.latest_update_markdown(second, item["selector"]) == ("# first", item["filename"])
    assert len(item["selector"]) + len("upd_html_") <= 64
    assert len(list((root / "profile").glob("forum-update-*.md"))) == 2


def test_old_links_survive_restart_and_mtime_changes(history):
    store, _ = history
    first = bot.save_completed_update(store.get_user(123), "# first")
    selector = bot.stored_update_items(first)[0]["selector"]
    second = bot.save_completed_update(first, "# second")
    for path in bot.saved_update_files(second):
        os.utime(path, (1, 1))
    reopened = bot.Store(store.db_path)
    try:
        assert bot.latest_update_markdown(reopened.get_user(123), selector)[0] == "# first"
    finally:
        reopened.conn.close()


def test_legacy_missing_and_foreign_links_never_return_latest(history):
    store, _ = history
    first = bot.save_completed_update(store.get_user(123), "# first")
    selector = bot.stored_update_items(first)[0]["selector"]
    second = bot.save_completed_update(first, "# second")
    foreign = bot.save_completed_update(store.get_user(456), "# foreign")
    for bad in ("0", "1", "-1", "../123", "fmissing", selector):
        assert bot.latest_update_markdown(foreign, bad) is None
    bot.selected_update_path(second, selector).unlink()
    assert bot.latest_update_markdown(second, selector) is None


def test_storage_disabled_link_expires_instead_of_switching(history):
    store, _ = history
    user = store.update_user(123, keep_files=0)
    first = bot.save_completed_update(user, "# first")
    selector = bot.stored_update_items(first)[0]["selector"]
    assert bot.latest_update_markdown(first, selector)[0] == "# first"
    second = bot.save_completed_update(first, "# second")
    assert bot.latest_update_markdown(second, selector) is None
    assert bot.saved_update_files(second) == []


def test_profile_exports_only_owner_and_preserves_revisions(history):
    store, root = history
    user = bot.save_completed_update(store.get_user(123), "# first")
    selector = bot.stored_update_items(user)[0]["selector"]
    bot.save_completed_update(store.get_user(456), "# foreign")
    bot.write_selected_update_markdown(user, selector, "# first\n\n## Личный план действий\n\nПлан", user["last_update_filename"])
    bot.sync_profile_updates()
    bot.sync_profile_updates()
    files = list((root / "profile").glob("forum-update-*.md"))
    assert len(files) == 1
    contents = [f.read_text() for f in files]
    archived = list((root / "profile/superseded").glob("forum-update-*.md"))
    assert len(archived) == 1 and archived[0].read_text() == "# first\n"
    assert any("План" in text for text in contents)
    assert all("foreign" not in text for text in contents)
    assert all(f.name in (root / "profile/README.md").read_text() for f in files)


def test_failed_profile_export_retries_without_losing_update(history, monkeypatch):
    store, root = history
    exporter = bot.export_snapshot

    def fail(*args):
        raise PermissionError("private path")

    monkeypatch.setattr(bot, "export_snapshot", fail)
    user = bot.save_completed_update(store.get_user(123), "# retained")
    assert bot.latest_update_markdown(user)[0] == "# retained"
    monkeypatch.setattr(bot, "export_snapshot", exporter)
    bot.sync_profile_updates()
    assert len(list((root / "profile").glob("forum-update-*.md"))) == 1


def test_old_md_and_html_downloads_use_same_selected_source(history, monkeypatch):
    store, _ = history
    old = bot.save_completed_update(store.get_user(123), "# first\n\n## Я\n\n**Вопрос**\n\nOld unique content")
    selector = bot.stored_update_items(old)[0]["selector"]
    user = bot.save_completed_update(old, "# second\n\nNew unique content")
    sent = []

    async def noop(*args, **kwargs):
        pass

    async def send(_update, payload, **kwargs):
        sent.append(payload.decode("utf-8-sig"))

    monkeypatch.setattr(bot, "reply", noop)
    monkeypatch.setattr(bot, "send_chat_action", noop)
    monkeypatch.setattr(bot, "send_temp_document", send)
    asyncio.run(bot.send_saved_update_files(None, user, selector))
    asyncio.run(bot.send_readable_update_file(None, user, selector))
    assert len(sent) == 2
    assert all("Old unique content" in text and "New unique content" not in text for text in sent)


@pytest.mark.parametrize("markdown,registered", [("# Форум-апдейт — Группа\n\n## Я\n\nОтвет", True), ("# Личные заметки\n\nТекст", False)])
def test_uploaded_update_is_registered_but_unrelated_markdown_is_not(history, monkeypatch, markdown, registered):
    store, root = history
    user = store.get_user(123)

    async def download(custom_path):
        Path(custom_path).write_text(markdown)

    async def get_file():
        return SimpleNamespace(download_to_drive=download)

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(store, "ensure_user", lambda update: user)
    monkeypatch.setattr(bot, "reply", noop)
    update = SimpleNamespace(effective_message=SimpleNamespace(document=SimpleNamespace(file_name="update.md", get_file=get_file)))
    asyncio.run(bot.handle_document(update, None))
    assert bool(bot.saved_update_files(user)) == registered
    assert (root / "profile/README.md").exists() == registered
