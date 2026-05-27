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

## Server Deploy

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
