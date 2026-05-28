# profile-cabinet Specification

## Purpose
TBD - created by archiving change add-onboarding-skip-and-profile-cabinet. Update Purpose after archive.
## Requirements
### Requirement: Personal cabinet
The bot SHALL provide a Telegram-native personal cabinet showing the user's
stored business club, full name, forum group, methodology, report recipient
username, file retention preference, next forum date, and diary status.
The personal cabinet SHALL let the user download the latest forum update as a
Markdown file.

#### Scenario: User opens personal cabinet
- **WHEN** an onboarded user chooses "Личный кабинет"
- **THEN** the bot shows the stored profile values and edit buttons

### Requirement: Profile field editing
The bot SHALL let the user edit business club, full name, forum group,
methodology, report recipient username, file retention preference, and next
forum date from the personal cabinet.

#### Scenario: User edits a field
- **WHEN** the user chooses a field and sends a new value or selects a button
- **THEN** the bot stores the new value and returns to the personal cabinet

### Requirement: Skipped profile fields remain editable
The bot SHALL mark skipped or defaulted fields in the personal cabinet so users
can replace them later.

#### Scenario: User skipped report recipient
- **WHEN** the personal cabinet is opened and report recipient username is empty
- **THEN** the bot shows that reports will stay in the private chat until the
  field is configured

#### Scenario: User downloads latest update
- **WHEN** the user presses "Скачать апдейт" in the personal cabinet
- **THEN** the bot sends the latest forum update as a `.md` file when it exists
- **AND** explains that no update is available when no update has been prepared yet
