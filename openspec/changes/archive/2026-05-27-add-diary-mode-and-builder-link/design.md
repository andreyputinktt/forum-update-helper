## Context

The bot already has profile onboarding, long questionnaire flows, voice
transcription, and optional OpenAI reflection. Diary mode should reuse those
building blocks without interfering with active update/health flows.

## Goals / Non-Goals

**Goals:**

- Make "build your own bot" discoverable from the bot info screen.
- Keep a concise AI-readable customization guide in the repo.
- Let users enable diary mode and define their own feedback lens.
- Let users change the diary feedback prompt any time from the menu.
- Process free-form diary entries only when no explicit flow/state is active.

**Non-Goals:**

- Long-term diary archive UI.
- Private psychological diagnosis or medical advice.
- Complex multi-prompt routing.

## Decisions

- Store `diary_enabled` and `diary_feedback_prompt` on `users`.
  Existing SQLite databases are migrated by checking columns at startup and
  applying `ALTER TABLE` when needed.
- Use a simple menu action `Режим дневника` plus `Промпт дневника`.
  This keeps the feature discoverable and allows prompt edits without commands.
- Reuse the existing OpenAI Responses API helper style for diary feedback.
  If OpenAI is not configured, the bot stores the mode but asks for text-only
  use without AI feedback.

## Risks / Trade-offs

- Free-form messages can be mistaken for diary entries after mode is enabled →
  the menu clearly states diary mode is on, and `/cancel` still stops active
  flows.
- Prompt can ask for unsafe feedback → system instruction keeps feedback
  non-medical, non-diagnostic, and experience/reflection oriented.
