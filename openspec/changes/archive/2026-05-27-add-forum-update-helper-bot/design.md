## Context

`ForumUpdateHelperBot` is a new standalone assistant in `assistants/`, deployed
as a polling Telegram bot on the kt.team server. It serves external forum-group
participants, so it cannot inherit the private "only Andrey" gate used by most
personal assistants. It still needs strong privacy basics: no secrets in git,
explicit data deletion, bounded logs, and minimal admin notifications.

The X-Competence source format lives in
`../../about-aputin/forum-updates/forum_update.md`. Runtime treats it as a
source of product requirements, not as writable memory.

## Goals / Non-Goals

**Goals:**

- Provide a complete Telegram-native onboarding and menu experience.
- Accept text, voice, and audio inputs; show voice transcripts before using
  them in flows.
- Guide the full X-Competence update questionnaire and return a Markdown update
  artifact.
- Ask for each user's next forum date during onboarding and use it for
  pre-forum and post-forum reminders.
- Collect next-morning forum-group health feedback and attempt to send a report
  to the configured Telegram community chat.
- Send only full name and business club to the admin when onboarding completes.
- Allow users to delete their stored data.

**Non-Goals:**

- Reading arbitrary Telegram group history. The Bot API cannot do this unless
  the bot is added to the group and receives messages.
- Booking hotels or calling travel APIs. The quarterly offsite prompt gives a
  curated prompt and venue-selection criteria.
- Multi-admin CRM or paid subscriptions.

## Decisions

1. Use `python-telegram-bot[job-queue]`.
   - Existing assistant repos already use it, it supports polling, jobs, inline
     keyboards, reply keyboards, and async handlers.
   - Alternative: aiogram. It is strong for large async bots, but would add a
     new local pattern.

2. Use SQLite for runtime state.
   - Profiles, flow state, interactions, and reminder dedupe fit one local DB.
   - Alternative: JSON files. Simpler initially, but riskier for concurrent
     updates from Telegram handlers and scheduled jobs.

3. Use a daily maintenance job instead of one systemd timer per user.
   - This keeps scheduling restart-safe and deduplicated through
     `reminder_log`.
   - Alternative: persistent job queue. It adds complexity without enough value
     for date-based reminders.

4. Treat community chat as user-provided `chat_id` or `@username`.
   - Telegram only allows sends when the bot can access that chat. Failures are
     reported to the user and logged.
   - Alternative: force numeric chat IDs. More reliable but less native for
     non-technical participants.

5. Keep OpenAI optional except for voice transcription and AI reflection.
   - Core questionnaire works without LLM calls. If `OPENAI_API_KEY` is absent,
     voice fails clearly and reflection is skipped.
   - Alternative: require OpenAI for every step. Better coaching, less robust.

## Risks / Trade-offs

- Telegram cannot send first to `@utandr` by username → require
  `ADMIN_CHAT_ID` after the admin starts the bot or runs `/getid`.
- Community report delivery can fail if the bot is not in the community chat →
  bot tells the user how to fix it and keeps the report in the conversation.
- Voice transcription may fail on `.oga`/`.opus` extensions → downloaded audio
  is normalized to `.ogg`.
- The full questionnaire is long → use stateful one-question-at-a-time flow,
  progress counters, a "Далее" button after each answer, and `/cancel`.
- Deleting data removes local DB rows and retained files, but Telegram messages
  already delivered to chats cannot be recalled by the bot.

## Migration Plan

1. Create repo files, env template, systemd unit, and deploy docs.
2. Configure server `.env` with `TELEGRAM_BOT_TOKEN`, OpenAI key, and
   `ADMIN_CHAT_ID`.
3. Install dependencies in a venv and start `forum-update-helper-bot.service`.
4. Admin opens the bot once and runs `/getid` if `ADMIN_CHAT_ID` is unknown.
5. Rollback: stop the service and restore the previous git revision; SQLite
   state remains in ignored `data/` unless explicitly deleted.
