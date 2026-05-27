## Context

The existing `UPDATE_QUESTIONS` list represents the YPO/X-Competence flow. The
classic format is shorter and centered on monthly ratings, significant events,
importance, feelings, and a current situation/opportunity to discuss. The
provided common materials cover forum communication rules, emotions, Johari
window, 5% rule, triangle of forum needs, constitution, and topics not suited
for forum.

## Goals / Non-Goals

**Goals:**

- Preserve the current YPO flow.
- Add a classic update flow without changing old saved answers.
- Keep reference materials local, versioned, and readable by humans and AI.
- Let users ask forum-guide questions through the bot.
- Let reflection evaluate updates using methodology and guide materials.

**Non-Goals:**

- OCR automation for future uploads.
- Full scanned-image storage in git.
- Perfect transcription of cropped/unreadable photo fragments.

## Decisions

- Store reference Markdown in `docs/forum-guide/`.
  It is easier to review, version, and include in prompts than embedding it in
  Python constants.
- Add `methodology` to `users` with default `YPO`.
  Existing users keep current behavior unless they choose `Классическая`.
- Define `CLASSIC_UPDATE_QUESTIONS` separately and choose questions at runtime.
  This avoids rewriting YPO prompt keys.
- Use a `guide` flow for free-form questions about the forum reference.

## Risks / Trade-offs

- Some source photos are cropped or partially unreadable → Markdown notes mark
  uncertain fragments instead of inventing text.
- Prompt context can grow → reference text is clipped before OpenAI calls.
