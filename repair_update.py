"""Prepare a reviewed replacement; apply only if the selected source is unchanged.

Run on the bot host: python repair_update.py USER_ID FILENAME [--apply]
No Telegram messages. Candidate and immutable backup live under ignored data/repairs/.
"""

import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

import main as bot
from profile_export import atomic_write


def checksum(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_answers(markdown):
    source = bot.editorial.read_source(markdown)
    if source:
        return source["answers"], source["mentor"]
    answers = bot.parse_update_markdown_answers(markdown, bot.UPDATE_QUESTIONS + bot.UNIFIED_UPDATE_QUESTIONS)
    match = re.search(r"^## Уточнения с ментором\n(.*?)(?=^## |\Z)", markdown, flags=re.M | re.S)
    dialogue = []
    if match:
        for item in re.finditer(r"\*\*(.*?)\*\*\s*\n\n(.*?)(?=\n\*\*|\Z)", match[1], flags=re.S):
            dialogue.append({"question": item[1].strip(), "answer": item[2].strip()})
    if not answers:
        raise ValueError("No recoverable answers")
    return answers, dialogue


async def prepare(user_id, filename):
    user = bot.store.get_user(user_id)
    selected = bot.latest_update_markdown(user, bot.update_selector(filename))
    if not selected or selected[1] != filename:
        raise ValueError("Source does not exist")
    markdown = selected[0]
    folder = bot.DATA_DIR / "repairs" / str(user_id) / Path(filename).stem
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "manifest.json").exists():
        raise ValueError("Repair already prepared; inspect existing candidate")
    answers, dialogue = source_answers(markdown)
    local_user = {**user, "flow_payload": json.dumps({"answers": answers, "mentor_dialogue": dialogue, "interview_version": 2})}
    candidate = await bot.compose_edited_update(local_user, answers, header=markdown.split("\n## ", 1)[0])
    candidate = bot.replace_personal_plan_section(candidate, bot.parse_personal_plan_answers(markdown))
    readable = bot.markdown_to_readable_html(candidate, title="Форумный апдейт")
    atomic_write(folder / "original.md", markdown)
    rows = bot.store.conn.execute("SELECT html_filename, html_content FROM readable_html_cache WHERE telegram_user_id=? AND source_filename=?", (user_id, filename)).fetchall()
    for row in rows:
        atomic_write(folder / Path(row["html_filename"]).name, row["html_content"])
    atomic_write(folder / "candidate.md", candidate)
    atomic_write(folder / "candidate.html", readable)
    atomic_write(folder / "manifest.json", json.dumps({"filename": filename, "user_id": user_id,
        "source_sha256": checksum(markdown), "candidate_sha256": checksum(candidate)}, indent=2))
    print(json.dumps({"prepared": str(folder), "visible_words": len(bot.editorial.visible_markdown(candidate).split())}))


def apply(user_id, filename):
    folder = bot.DATA_DIR / "repairs" / str(user_id) / Path(filename).stem
    manifest = json.loads((folder / "manifest.json").read_text())
    user = bot.store.get_user(user_id)
    selector = bot.update_selector(filename)
    selected = bot.latest_update_markdown(user, selector)
    candidate = (folder / "candidate.md").read_text(encoding="utf-8")
    if (manifest["filename"], manifest["user_id"]) != (filename, user_id):
        raise ValueError("Wrong repair identity")
    if not selected or checksum(selected[0]) != manifest["source_sha256"]:
        raise ValueError("Source changed; refusing stale repair")
    if checksum(candidate) != manifest["candidate_sha256"] or not bot.editorial.read_source(candidate):
        raise ValueError("Candidate changed or missing provenance")
    # Update the existing identity so old list links retrieve the corrected version.
    bot.write_selected_update_markdown(user, selector, candidate, filename)
    cache_key = bot.readable_html_cache_key(candidate)
    readable = bot.markdown_to_readable_html(candidate, title="Форумный апдейт")
    bot.store.conn.execute("DELETE FROM readable_html_cache WHERE telegram_user_id=? AND source_filename=?", (user_id, filename))
    bot.store.conn.commit()
    bot.store.save_readable_html_cache(user_id, cache_key, filename, bot.readable_html_filename(filename, cache_key), readable)
    print(json.dumps({"applied": filename, "cache_key": cache_key, "backup": str(folder / "original.md")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("user_id", type=int)
    parser.add_argument("filename")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if Path(args.filename).name != args.filename:
        parser.error("filename must be a basename")
    if args.apply:
        apply(args.user_id, args.filename)
    else:
        asyncio.run(prepare(args.user_id, args.filename))
