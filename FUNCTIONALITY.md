# forum-update-helper Functionality

This file is the service-owned contract for user-visible behavior, commands,
states, data contracts, constraints, and non-goals. Update it before changing
behavior, then verify the new behavior does not conflict with README.md,
DEV.md, weak coupling, privacy, testability, or mature AI/software design.

## Overview

Telegram-бот для подготовки к форуму по форматам YPO и X-Competence.

## Current Behavior

### Diary mode setup
- User enables diary mode
- User configures diary reminder

### Diary prompt editing
- User changes diary prompt

### Diary entry feedback
- User sends diary entry

### Forum guide reference
- Materials are saved

### Forum guide Q&A
- User asks guide question

### Update evaluation context
- User completes update

### Onboarding profile
- User completes onboarding
- User configures diary during onboarding
- Existing user restarts onboarding
- User skips identity fields
- User selects methodology
- User chooses AI-agent handoff
- User tries to skip methodology
- Admin receives minimal new-user notification

### Telegram-native menu
- User opens menu
- User opens a submenu

### Message pacing
- Bot sends consecutive messages

### Voice and audio transcription
- User answers by voice
- For the explicitly configured Telegram user ID, transcription reads the
  existing voice-dictation JSON lexicon without modifying it. Other users
  receive only the generic forum vocabulary; usernames do not grant access.
- A bounded set of canonical terms is selected using the current question,
  the user's current answers and the frozen previous-update context. Configured
  priority terms (e.g. a frequently misrecognized name) are included when they
  exist in the dictionary. Raw answers/source paths are not sent as ASR hints.
- The dictionary is read for each recording so refreshed vocabulary takes
  effect without restarting the bot. Missing/invalid vocabulary falls back to
  ordinary transcription with a diagnostic event that contains no private data.
- Hints bias recognition only when supported by the audio; there is no global
  replacement of “Джокер” with “Джеклин”, no rewriting of historical updates,
  and no extra text-generation pass. Audio deletion and profile export rules
  remain the same.

### X-Competence update flow
- User starts update preparation
- User advances through questions
- User sends multiple messages for one question
- User revisits a question
- User completes update preparation
- User keeps update files

### Unified X-Competence interview and mentor
- New X-Competence sessions use one 18-step interview, grouped by sphere:
  rating, meaningful positive event, meaningful difficult event, retrospective,
  next period; then main request, situation/attempts, and experience requested
  from the group. Events ask for facts, personal importance and feelings.
  Ratings, events and the request are reused for a traditional forum overview;
  the user does not fill two questionnaires.
- Each question displays a short, dated excerpt from the last completed update
  of the same forum group, or explicitly says that there is no previous answer.
  Retrospectives additionally recall the previous next-period plan. This source
  is frozen when the interview starts and is not copied into new answers.
  Users may explicitly reuse the displayed previous answer or clear a current
  answer; text and voice fragments remain supported. Skipping is explicit.
- After the questionnaire the mentor asks at most three adaptive, single
  questions about concrete events, feelings, personal meaning, tension and the
  key forum request. Each next question uses the actual preceding reply.
  No diagnoses, invented motives, leading interpretations or instructions to
  disclose unwanted details. The user may skip or save at any moment.
- Mentor questions, answers and progress are persisted in SQLite. Provider
  errors/timeouts use a short deterministic question without losing answers.
  Only completing/saving the interview creates a ready update and exports it.
- The final X-Competence Markdown is an edited first-person update: one section
  per sphere combining rating, significant positive/negative events, personal
  importance, stated feelings, retrospective and next steps; one final request
  incorporates mentor replies. No repeated questionnaire or separate AI review.
- Editing considers all current answers together and applies explicit author
  corrections before summarizing. Vocabulary is spelling context, not evidence.
  Observations, the author's interpretations and intentions remain distinct.
  Missing feelings are not invented; ambiguous numbers/transcription fragments
  are listed once as points to clarify, never silently repaired.
- Each generated field carries checked source quotations internally. Incomplete
  API responses or invalid structures are rejected; draft answers remain saved
  for retry, without exporting an unfinished update. Raw answers/mentor replies
  and cleaned answers are preserved in a versioned Markdown source comment for
  audit, editing and previous-answer reminders, excluded from readable HTML.
- A separate semantic audit compares the edited claims against the full original
  answers and mentor replies, checking negation, comparisons, numbers, attribution,
  explicit corrections and invented causal links. Its grounded corrections are
  applied before saving; failed/incomplete audit keeps the draft for retry.
- HTML renders that edited Markdown deterministically, without truncating text
  or running a second summarizer. The cache version changes with the renderer.
- Mentor rounds have distinct purposes: a concrete moment and feelings, its
  personal meaning/need, then only any unresolved forum request. Already clear
  requests are not reformulated three times.
- In-progress legacy questionnaires keep their existing question order/answers
  across deployment; a short completion step collects missing traditional
  event answers before the mentor. Saved historical prompts remain parseable.

### Stable update history
- Each completed or uploaded update receives a unique filename, even when two
  updates finish within the same minute. Editing previous answers creates a new
  completed version; earlier files remain available.
- List links identify a specific filename within the authenticated user's
  history. Adding files, changing modification times or restarting the bot must
  not change what a link downloads, edits or adds a personal plan to.
- Old positional links are rejected with a request to reopen “Мои апдейты”;
  they cannot be resolved safely after the list changes. Missing files never
  fall back to the latest update. With file storage disabled only the current
  database copy remains available; expired links fail explicitly.
- Uploaded `.md` files in the bot's forum-update format are registered as ready
  updates. Other attachments retain the existing file-storage behavior.

### Personal profile export (explicit owner opt-in)
- The configured Telegram user ID (Andrey's `utandr`, verified during setup)
  exports completed/uploaded Markdown updates to the configured profile folder
  `about-aputin/forum-updates/from-bot/`. Username changes do not transfer access.
- Export copies the source Markdown and maintains a local README index. Distinct
  updates and revisions have separate snapshot files. Draft answers, diary
  messages, unrelated attachments and other users' updates are excluded.
- The index identifies the current revision of each source filename; superseded
  snapshots live in a clearly labelled archive and are not current profile input.
- Export runs after completion/upload/personal-plan changes, at startup and
  during daily maintenance to backfill retained history and retry failures.
  Repeating an export is idempotent. Export failure does not lose the bot's update.
- This is an explicitly configured data export, disabled by default. It does
  not modify psychological interpretations or existing hand-curated snapshots.
  Profile copies are independently retained personal records; bot data deletion
  does not remove them or their Git history.

### Forum date reminders
- Pre-forum reminder is due
- Post-forum health check is due

### Quarterly offsite reminder
- Offsite reminder is due

### Forum-group health report
- User completes health check

### Privacy deletion
- User confirms deletion

### Runtime logging and counters
- Admin requests stats

### Personal cabinet
- User opens personal cabinet

### Profile field editing
- User edits a field

### Skipped profile fields remain editable
- User skipped report recipient
- User deletes data from personal cabinet

## Change Intake Rule

Before implementation, write the intended behavior here. If the requested
change conflicts with weak coupling, service purpose, privacy/secrets,
testability, or robust AI/software engineering design, stop and ask for
confirmation with a concise explanation and a better alternative.

## Migrated Backlog / Historical Changes

- No active historical changes were migrated.

## Non-Goals

- Do not mix runtime code with personal memory/data layers.
- Do not duplicate shared domain logic inside Telegram transport adapters.
- Do not expose secrets, private paths, or raw private source data in user-facing responses.
