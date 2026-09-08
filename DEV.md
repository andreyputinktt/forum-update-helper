# Development

Следовать корневым правилам `../../DEV.md` и `../DEV-telegram.md`.

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 -m py_compile main.py tests/test_forum_update_helper.py
python3 -m pytest
openspec validate add-forum-update-helper-bot --strict
```

Локальные web-серверы не запускать.

## Environment

```env
TELEGRAM_BOT_TOKEN=
ADMIN_CHAT_ID=
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.5
OPENAI_HTML_MODEL=gpt-5.5
OPENAI_TRANSCRIBE_MODEL=gpt-4o-transcribe
OPENAI_TRANSCRIBE_LANGUAGE=ru
OPENAI_REFLECTION_ENABLED=true
TIMEZONE=Europe/Moscow
DATA_DIR=./data
DAILY_MAINTENANCE_TIME=09:30
OFFSITE_INTERVAL_DAYS=90
PRE_FORUM_REMINDER_DAYS=3
TELEGRAM_TEXT_LIMIT=3900
```

`ADMIN_CHAT_ID` нужен для уведомлений Андрею о новых пользователях. Telegram Bot
API не позволяет надёжно писать первым по `@username`; админ должен открыть бота
и выполнить `/getid`.

### Персональный словарь распознавания

Read-only источник — JSON словаря [voice-dictation](../../voice-dictation/README.md).
В ignored `.env` форум-бота:

```env
TRANSCRIPTION_LEXICON_USER_ID=195130338
TRANSCRIPTION_LEXICON_PATH=/home/a.putin/GIT/voice-dictation/data/personal-lexicon.json
TRANSCRIPTION_PRIORITY_TERMS=Джеклин
```

По умолчанию выключено. `transcription_vocabulary.py` читает контракт
`{"terms": [{"term": "...", "aliases": [], "weight": 1, "frequency": 1}]}`;
также принимает корневой список. Выбирает до 32 канонических терминов / 1800
символов: приоритетные, совпадающие с контекстом вопроса/ответов, затем по весу.
Aliases помогают выбору, но в ASR отправляются правильные написания. Приватные
пути `last_seen` и исходные ответы в prompt не передаются. Доступ проверяется
по Telegram ID, не по username. Только существующие записи могут стать подсказками.

Словарь перечитывается для каждого аудио; его обновляет владелец voice-dictation,
форум-бот ничего туда не пишет. Нет/повреждён файл — обычный prompt и warning
с типом ошибки без приватных данных. Лог успешного выбора содержит user ID и
число терминов. Слова не заменяются постфактум; оценивать качество следует на
реальных аудио с именами и контрольных примерах вроде фильма «Джокер».

## Derived Artifacts And DRY

### Единое интервью и ментор

`interview.py` — спецификации 18 вопросов, проверка дословных фрагментов и
традиционное представление. `main.py` — хранение состояния и Telegram/AI вызовы.
Новые X-Competence сценарии сохраняют `interview_version: 2` в `flow_payload`.
Сценарии `update` без этой версии продолжают исходную последовательность
`UPDATE_QUESTIONS`; при завершении `update_extra` собирает недостающие шесть
событий. Исторические тексты вопросов остаются алиасами при разборе Markdown.

Снимок `previous_update` содержит ответы, дату и имя последнего готового файла
той же форум-группы. Он фиксируется на старте и не меняется от появления новых
файлов; старые активные сценарии получают снимок при показе следующего вопроса.
Фрагменты ограничены с учётом HTML-экранирования. Ответы не переносятся без кнопки.

Состояние `update_mentor` хранит `mentor_dialogue` (вопрос/ответ) и номер шага
в SQLite. Следующий вопрос генерируется только после кнопки; stale-кнопки с
номером прошлого шага игнорируются. Лимит — три вопроса, раннее сохранение
доступно на любом шаге. Перезапуск сохраняет этот сценарий. Финальное сохранение
использует существующий `save_completed_update` и экспорт профиля.

AI-вызовы уточнений и структурирования используют `OPENAI_MODEL`, timeout 35 s
и no retry; сбой не теряет данные. Чувства и важность в традиционном представлении
принимаются только как дословные фрагменты ответа. При нехватке информации поле
остаётся «Не уточнено», рядом сохраняется исходный ответ; уточнения ментора —
отдельный раздел, без неподтверждённого переноса чувств между событиями.

Регрессии: `tests/test_unified_interview.py` — обе методики, legacy-сценарии,
история по группе, copy/clear, сохранение диалога, ранний выход, provider fallback,
grounded extraction и Markdown/HTML. Общая проверка: `.venv/bin/python -m pytest -q`.

Markdown-файл апдейта (`.md`) — единственный source of truth для содержимого
апдейта. Производные артефакты, например короткая HTML-версия для чтения на
форуме, не должны становиться независимой копией данных или отдельной веткой
логики.

Для таких артефактов использовать кэш по контрольной сумме источника и версии
генератора:

- cache key включает нормализованный исходный `.md`, версию алгоритма,
  используемую модель/режим генерации;
- при неизменном `.md` повторная выдача берётся из кэша без нового AI-запроса;
- при любом изменении `.md` checksum меняется, и артефакт пересобирается;
- fallback-результат после временной ошибки AI не кэшировать как полноценную
  AI-версию;
- при изменении prompt/HTML-алгоритма поднимать версию кэша
  (`HTML_BRIEF_CACHE_VERSION`), чтобы старые производные файлы не выдавались
  как актуальные;
- кэш удалять вместе с данными пользователя.

Это правило защищает DRY: данные и правила генерации описаны в одном месте, а
кэш только ускоряет повторную выдачу производного результата.

## Server Deploy

### Экспорт апдейтов в личный профиль

Экспорт выключен по умолчанию. Для согласованного пользователем экспорта Андрея
в ignored `.env` сервиса настроены:

```env
PROFILE_EXPORT_USER_ID=195130338
PROFILE_EXPORT_DIR=/home/a.putin/GIT/about-aputin/forum-updates/from-bot
```

ID проверен по `utandr` в базе сервиса; username не является ключом доступа.
`profile_export.py` копирует исходный Markdown в отдельные снимки с checksum
содержимого и ведёт README только в выделенной папке. Старые ручные материалы
профиля не меняются. Черновики не экспортируются. Завершение, загрузка Markdown
и добавление плана запускают экспорт; startup/daily maintenance повторяют его
для сохранённой истории. Ошибки журналируются без приватных путей и текста.
Повторный запуск не создаёт дубликатов. При отключённом хранении файлов повторно
доступна только последняя версия из SQLite. Архив профиля хранится независимо
от удаления данных в боте, включая его Git-историю.

После включения настроек перезапустить `forum-update-helper-bot`; startup
импортирует уже сохранённые апдейты, не трогая активный сценарий. Изменения в
`about-aputin` синхронизируются штатным Git/autosync этого репозитория.

Canonical server path:

```text
/home/a.putin/GIT/assistants/forum-update-helper
```

Systemd unit template: `systemd/forum-update-helper-bot.service`.

Первый deploy:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполнить .env без коммита секретов
sudo install -m 0644 systemd/forum-update-helper-bot.service /etc/systemd/system/forum-update-helper-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now forum-update-helper-bot
journalctl -u forum-update-helper-bot -f
```

Обновления tracked-кода на сервере идут через git/autosync, не через `scp` или
`rsync`.
