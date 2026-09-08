"""Read-only adapter for the existing voice-dictation lexicon JSON contract."""

import json
import logging
import math
import re
from pathlib import Path

log = logging.getLogger("forum_update_helper")


def words(text):
    return set(re.findall(r"[\w]+", str(text).casefold().replace("ё", "е")))


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else 0
    except (ValueError, TypeError):
        return 0


def select_terms(path: Path, context: str, priority_terms=(), limit=32, char_budget=1800):
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = raw.get("terms", []) if isinstance(raw, dict) else raw
        if not isinstance(entries, list):
            raise ValueError("Invalid lexicon terms")
    except (OSError, ValueError) as exc:
        log.warning("transcription lexicon unavailable error_type=%s", type(exc).__name__)
        return []

    context_words = words(context)
    pinned = {str(term).strip().casefold() for term in priority_terms}
    scored = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("term"), str):
            continue
        term = " ".join(entry["term"].split())
        if not 2 <= len(term) <= 100:
            continue
        aliases = entry.get("aliases", [])
        variants = [term] + ([alias for alias in aliases if isinstance(alias, str)] if isinstance(aliases, list) else [])
        matched = any(tokens and tokens <= context_words for variant in variants if (tokens := words(variant)))
        score = (term.casefold() in pinned, matched, number(entry.get("weight")), min(number(entry.get("frequency")), 20))
        scored.append((score, term))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected, seen, size = [], set(), 0
    for _, term in scored:
        if term.casefold() in seen or size + len(term) + 2 > char_budget:
            continue
        selected.append(term)
        seen.add(term.casefold())
        size += len(term) + 2
        if len(selected) >= limit:
            break
    return selected
