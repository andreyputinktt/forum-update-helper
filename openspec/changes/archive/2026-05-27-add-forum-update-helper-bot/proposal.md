## Why

Forum participants need a Telegram-native assistant that keeps the new
X-Competence update format close at hand: it should collect profile data,
guide a full pre-forum update, remind about dates and offsite strategy
sessions, and collect group-health reflection after the forum.

## What Changes

- Add a standalone `ForumUpdateHelperBot` Telegram service under
  `assistants/forum-update-helper/`.
- Add onboarding for business club, full name, forum group, reporting
  community chat, file retention preference, and the next forum date.
- Add Telegram-native menu buttons for update preparation, next forum date,
  post-forum health check, bot info, expert links, author contact, stats, and
  data deletion.
- Add voice/audio transcription with transcript echo before the text is used by
  the active flow.
- Add the full X-Competence question flow based on `about-aputin` forum update
  format and generate a Markdown update artifact.
- Add scheduled reminders: a three-days-before-forum update start,
  next-forum-date prompts, next-morning health check, and quarterly personal
  strategy offsite prompts.
- Add SQLite-backed profiles, flow state, interaction counters, reminder
  dedupe, and admin notification for new users with only full name and business
  club.
- Add env template, deployment docs, systemd unit, and checks.

## Capabilities

### New Capabilities

- `forum-update-helper-bot`: Telegram bot behavior, onboarding, update and
  health flows, reminders, privacy deletion, voice transcription, admin
  notification, and deployment contract.

### Modified Capabilities

- None.

## Impact

- New Python service using `python-telegram-bot[job-queue]`, OpenAI SDK,
  `python-dotenv`, `dateparser`, and SQLite.
- New runtime state under ignored `data/`.
- New systemd unit template for kt.team server deployment.
- No changes to existing assistants or `about-aputin`; the X-Competence format
  is copied into the bot contract as read-only source material.
