## Why

The bot currently guides only the YPO/X-Competence update flow. Forum groups
also use a classic monthly update format and shared forum principles, so users
need to choose a methodology and the bot needs a structured reference base for
answers and update evaluation.

## What Changes

- Save the provided photo materials as Markdown reference files.
- Add a methodology field with `YPO` and `Классическая` options.
- Ask for methodology during onboarding and allow changing it in the personal
  cabinet.
- Use methodology-specific questions when preparing an update.
- Include the forum guide and selected methodology reference in AI reflection
  and diary-style answers about forum rules.
- Add a "Справочник форума" action for asking questions about the saved
  materials.

## Capabilities

### New Capabilities

- `forum-guide`: saved Markdown reference materials and forum-guide Q&A.

### Modified Capabilities

- `forum-update-helper-bot`: add methodology selection and use the selected
  methodology for update preparation and reflection.
- `profile-cabinet`: show and edit the selected methodology.

## Impact

- Extends SQLite `users` with `methodology`.
- Adds Markdown docs under `docs/forum-guide/`.
- Extends update flow routing and OpenAI prompt context.
