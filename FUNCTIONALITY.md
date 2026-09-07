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

### X-Competence update flow
- User starts update preparation
- User advances through questions
- User sends multiple messages for one question
- User revisits a question
- User completes update preparation
- User keeps update files

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
