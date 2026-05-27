## Why

Onboarding must be low-friction: users should be able to skip any setup field
and still start using the bot. They also need a Telegram-native personal
cabinet to fix skipped or incorrect profile values later.

## What Changes

- Add "Пропустить" buttons for onboarding steps.
- When business club, full name, forum group, community chat, file retention, or
  next forum date are skipped, store sensible defaults so onboarding can finish.
- For skipped identity/forum fields, use a generated anonymous name, generated
  forum-group name, and business club "Другое".
- Add "Личный кабинет" menu and inline actions.
- Let users edit business club, full name, forum group, community chat, file
  retention preference, and next forum date from the personal cabinet.

## Capabilities

### New Capabilities

- `profile-cabinet`: Telegram-native profile viewing and editing.

### Modified Capabilities

- `forum-update-helper-bot`: onboarding becomes skippable with defaults and the
  menu includes access to the personal cabinet.

## Impact

- Extends text/callback routing and onboarding flow.
- Keeps existing SQLite schema; no migration needed.
- Updates README and OpenSpec specs.
