"""Opt-in Markdown snapshot export; no Telegram or profile interpretation logic."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(content)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def export_snapshot(directory: Path, filename: str, markdown: str) -> bool:
    """Keep immutable revisions and rebuild only our dedicated directory's index."""
    content = markdown.lstrip("\ufeff").strip() + "\n"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", Path(filename).stem)
    target = directory / f"{stem}-{digest}.md"
    directory.mkdir(parents=True, exist_ok=True)
    changed = not target.exists()
    if changed:
        atomic_write(target, content)
    # Only one current snapshot per source. Retain old bytes in an explicit archive.
    previous = [p for p in directory.glob(f"{stem}-*.md") if p != target]
    if previous:
        archive = directory / "superseded"
        archive.mkdir(exist_ok=True)
        atomic_write(archive / "README.md", "# Заменённые версии\n\nИсторические снимки, заменённые исправленными апдейтами.\nНе использовать как актуальные сведения психологического профиля. Текущие версии — в родительской папке.\n")
        for path in previous:
            os.replace(path, archive / path.name)
    names = sorted((p.name for p in directory.glob("*.md") if p.name != "README.md"), reverse=True)
    index = (
        "# Готовые апдейты из ForumUpdateHelperBot\n\n"
        "Автоматические снимки завершённых и загруженных апдейтов Андрея (@utandr).\n"
        "Источник: forum-update-helper; черновики и данные других участников исключены.\n"
        "Дата в имени — дата сохранения в боте; дата форума внутри может быть устаревшей.\n"
        "Ниже только актуальная версия каждого апдейта; заменённые снимки — в superseded/, не использовать их как актуальный контекст.\n"
        "Это исходные апдейты для психологического контекста, без автоматических выводов.\n\n"
        "В новых файлах анализировать видимую редакцию. Скрытый блок forum-update-source-v1 содержит сырьё, в том числе исправленные ошибки распознавания; это не самостоятельные факты профиля.\n\n"
        + "\n".join(f"- [{name}]({name})" for name in names) + "\n"
    )
    readme = directory / "README.md"
    if not readme.exists() or readme.read_text(encoding="utf-8") != index:
        atomic_write(readme, index)
    return changed
