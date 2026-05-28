# diary-mode Specification

## Purpose
TBD - created by archiving change add-diary-mode-and-builder-link. Update Purpose after archive.
## Requirements
### Requirement: Diary mode setup
The bot SHALL let an onboarded user enable diary mode from the Telegram menu and
SHALL ask what perspective/prompt to use for diary feedback before enabling it.
The bot SHALL let users configure a daily diary reminder for either 21:00 on the
same day or 08:00 on the following day.

#### Scenario: User enables diary mode
- **WHEN** an onboarded user chooses "Режим дневника"
- **THEN** the bot asks for a feedback prompt, stores it, enables diary mode,
  and confirms how diary entries will be handled

#### Scenario: User configures diary reminder
- **WHEN** a user chooses a diary reminder time
- **THEN** the bot stores the reminder time and sends at most one diary prompt per configured day and time

### Requirement: Diary prompt editing
The bot SHALL let a user change the diary feedback prompt from the menu at any
time.

#### Scenario: User changes diary prompt
- **WHEN** a user chooses "Промпт дневника" and sends a new prompt
- **THEN** the bot stores the new prompt and uses it for future diary feedback

### Requirement: Diary entry feedback
The bot SHALL treat free-form text or transcribed voice as a diary entry and
reply with feedback based on the stored diary prompt when diary mode is enabled
and no onboarding state or explicit flow is active.

#### Scenario: User sends diary entry
- **WHEN** diary mode is enabled and the user sends a free-form entry
- **THEN** the bot replies with concise feedback using the user's stored prompt
