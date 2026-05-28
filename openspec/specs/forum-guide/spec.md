# forum-guide Specification

## Purpose
TBD - created by archiving change add-methodology-selector-and-forum-guide. Update Purpose after archive.
## Requirements
### Requirement: Forum guide reference
The bot SHALL keep local Markdown reference materials for common forum rules and
classic and X-Competence update formats. Source images SHALL NOT be required or
stored in the repository for guide usage; the bot SHALL use the Markdown text.

#### Scenario: Materials are saved
- **WHEN** the repository is checked out
- **THEN** common forum materials, classic update format, and X-Competence
  update format are available as Markdown files under `docs/forum-guide/`

### Requirement: Forum guide Q&A
The bot SHALL let users ask questions based on the saved forum guide materials.

#### Scenario: User asks guide question
- **WHEN** a user opens "Справочник форума" and sends a question
- **THEN** the bot answers using the saved guide materials as context

### Requirement: Update evaluation context
The bot SHALL include the selected methodology and saved guide materials when
generating AI reflection for a completed update.

#### Scenario: User completes update
- **WHEN** an update flow is completed
- **THEN** the AI reflection evaluates the update against the selected
  methodology and forum principles
