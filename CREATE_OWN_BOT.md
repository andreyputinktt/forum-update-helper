# Сделать собственный бот

Эта инструкция написана для ИИ-агента, которому нужно быстро сделать похожего
Telegram-бота для другого человека, клуба или методики.

## Задача для ИИ

Склонируй этот репозиторий как шаблон и адаптируй его под нового владельца:

```text
Сделай для меня Telegram-бота на базе forum-update-helper.
Возьми архитектуру, SQLite-хранилище, voice transcription, меню, reminders,
privacy deletion и systemd deploy. Замени методику, onboarding, вопросы,
ссылки, автора, admin chat id и тексты под мой кейс. Секреты не коммить.
```

## Что менять

- `main.py`
  - `BUSINESS_CLUBS`
  - `MAIN_KEYBOARD`
  - URL-ссылки и тексты автора
  - списки `UPDATE_QUESTIONS` / `HEALTH_QUESTIONS`
  - reminder schedule и тексты уведомлений
- `.env.example`
  - имена env-переменных и дефолты
- `README.md`
  - описание продукта, команды и privacy contract
- `systemd/*.service`
  - имя сервиса, путь проекта и user
- `openspec/specs/`
  - поведенческий контракт нового бота

## Минимальный deploy

1. Создать нового Telegram-бота через BotFather.
2. Создать новый private repo.
3. Заполнить `.env` на сервере:

```env
TELEGRAM_BOT_TOKEN=
ADMIN_CHAT_ID=
OPENAI_API_KEY=
TIMEZONE=Europe/Moscow
```

4. Поставить зависимости:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m pytest
```

5. Установить systemd unit и стартовать сервис.

## Правила безопасности

- Bot token, OpenAI key и admin chat id не писать в git.
- В admin-уведомления отправлять только минимальные данные.
- Оставить кнопку удаления данных.
- Если бот отправляет отчёты в Telegram community, объяснить пользователю, что
  бот должен быть добавлен в чат и иметь право писать.
