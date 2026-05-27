## Why

Users who like the bot need a clear path to create their own version, and the
bot should also support lightweight diary reflections between forum updates with
user-defined feedback lenses.

## What Changes

- Add "Сделать собственный бот" to info materials, pointing to the repository.
- Add a short AI-facing instruction in the repository that explains how to clone
  this bot for another user/community.
- Add diary mode that can be enabled from the Telegram menu.
- Ask the user how to give diary feedback, store that prompt, and let the user
  change it later from the menu.
- When diary mode is enabled and no other flow is active, treat free-form text
  or voice as a diary entry and answer with feedback using the stored prompt.

## Capabilities

### New Capabilities

- `diary-mode`: user-configurable diary feedback mode.

### Modified Capabilities

- `forum-update-helper-bot`: add builder-link info material and menu entries
  for diary mode/prompt management.

## Impact

- Extends the SQLite `users` table with diary settings.
- Adds optional OpenAI feedback for diary entries.
- Updates README/AI instructions and Telegram about/menu copy.
