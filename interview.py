"""Question specifications and source-preserving traditional update rendering."""

import re

SPHERE_PAIRS = (("Моё дело", "Бизнес"), ("Моя семья / близкие", "Семья"), ("Я", "Личное"))
MENTOR_LIMIT = 3


def question_specs():
    for sphere, classic in SPHERE_PAIRS:
        yield f"rating_{sphere}", f"{sphere}: поставь оценку месяца 1–10. Что изменилось и почему?", sphere
        for sign, label in (("plus", "радостное"), ("minus", "трудное")):
            yield (
                f"classic_{classic}_{sign}",
                f"{sphere}: какое самое значимое {label} событие месяца?\n\n"
                "Можно одним голосовым или текстом:\n"
                "Событие: что конкретно произошло.\n"
                "Важность: почему это важно лично тебе.\n"
                "Чувства: назови до трёх чувств своими словами.\n\n"
                "Выбирай свои крайние 5% — то, чем действительно хочется поделиться. Если события нет, пропусти.",
                sphere,
            )
        yield (
            f"retrospective_{sphere}",
            f"{sphere}: что получилось из намеченного? Какое действие помогло, а что не сработало?\n\n"
            "События уже записаны — здесь достаточно выводов. Прошлый план показан ниже, если он есть.",
            sphere,
        )
        yield (
            f"next_period_{sphere}",
            f"{sphere}: что будет означать «отлично» через месяц?\n\n"
            "Свяжи с годовой целью. Назови один шаг в своей власти, поддержку и главное препятствие; что вне контроля?",
            sphere,
        )
    yield (
        "main_request_core",
        "Какая из названных тем волнует сильнее всего — что хочется вынести на форум?\n\n"
        "Сформулируй один вопрос «Как мне…?» в зоне своего контроля. Почему он важен сейчас?",
        "Главный запрос",
    )
    yield (
        "main_request_details",
        "Что группе нужно знать об этой ситуации?\n\n"
        "Коротко: когда началась; желаемый результат; что уже пробовал; какие варианты видишь и что останавливает.\n"
        "Если ничего не менять — что рискуешь потерять? Денежную цену назови только если она здесь уместна.",
        "Главный запрос",
    )
    yield (
        "main_request_help",
        "Какой личный опыт ты хочешь услышать от группы?\n\nНачни: «Поделитесь опытом, как вы…»",
        "Главный запрос",
    )


def event_keys():
    return [f"classic_{classic}_{sign}" for _, classic in SPHERE_PAIRS for sign in ("plus", "minus")]


def normalize(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def grounded_event_fields(answer, candidate=None):
    """Accept only excerpts found in the user's answer, never invented feelings."""
    result = {}
    candidate = candidate if isinstance(candidate, dict) else {}
    for key, label in (("event", "Событие"), ("importance", "Важность"), ("feelings", "Чувства")):
        match = re.search(rf"(?:^|\n)\s*{label}:\s*(.*?)(?=\n\s*(?:Событие|Важность|Чувства):|\Z)", answer, re.S | re.I)
        value = match.group(1).strip() if match else candidate.get(key, "")
        result[key] = value if isinstance(value, str) and normalize(value) in normalize(answer) else ""
    return result


def traditional_markdown(answers, fields):
    lines = ["## Традиционный форум — обзор месяца", "", "События, их личная важность и чувства. Пустые поля не додуманы за автора.", ""]
    for index, (sphere, classic) in enumerate(SPHERE_PAIRS):
        rating = answers.get(f"rating_{sphere}") or answers.get(("classic_business_rating", "classic_family_rating", "classic_personal_rating")[index])
        lines.extend([f"**{classic}: оценка месяца**", "", rating or "_Нет ответа_", ""])
        for sign, label in (("plus", "Плюс — радостные 5%"), ("minus", "Минус — трудные 5%")):
            key = f"classic_{classic}_{sign}"
            parts = grounded_event_fields(str(answers.get(key) or ""), fields.get(key))
            for field, title in (("event", "Событие"), ("importance", "Почему важно"), ("feelings", "Чувства")):
                lines.extend([f"**{classic}, {label}: {title}**", "", parts[field] or "_Не уточнено_", ""])
            if answers.get(key) and not all(parts.values()):
                lines.extend([f"**{classic}, {label}: исходный ответ**", "", str(answers[key]), ""])
    request = answers.get("main_request_core") or answers.get("classic_main_question") or answers.get("classic_presentation_topic")
    lines.extend(["**Главная тема / вопрос к форуму**", "", request or "_Нет ответа_", ""])
    return "\n".join(lines)
