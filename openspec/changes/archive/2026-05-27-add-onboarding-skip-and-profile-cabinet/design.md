## Context

The bot currently requires all onboarding fields before exposing the main menu.
That is too strict for Telegram use: a user may not know the forum date or
community chat at first launch. Existing profile fields already live in the
`users` SQLite table, so editing can reuse the same storage.

## Goals / Non-Goals

**Goals:**

- Allow skipping every onboarding step.
- Fill skipped fields with explicit defaults that keep reminders and reports
  predictable.
- Provide a personal cabinet with current values and edit buttons.
- Route edit answers through the same parsers used by onboarding.

**Non-Goals:**

- Multi-profile support per Telegram account.
- Rich web admin cabinet.
- Sending reports when community chat is skipped; the bot stores an empty value
  and explains that reports will stay in the private chat until configured.

## Decisions

- Use callback data `skip:<field>` for onboarding skip buttons.
  This keeps skip behavior native and avoids forcing users to type "skip".
- Store fallback values:
  - business club: `Другое`
  - full name: `Участник форума <telegram_user_id>`
  - forum group: `Форум-группа <telegram_user_id>`
  - community chat: empty string
  - keep files: `0`
  - next forum date: 30 days from today
- Use `state="profile:<field>"` for cabinet edits.
  This avoids adding new tables and keeps edit routing simple.

## Risks / Trade-offs

- A skipped forum date can create a reminder on an arbitrary date → default is
  clearly shown and can be edited immediately from the cabinet.
- Empty community chat means reports cannot be sent to a group → bot keeps the
  report in the private chat and the cabinet highlights the missing field.
