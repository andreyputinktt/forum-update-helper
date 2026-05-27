# forum-update-helper-bot Specification

## Purpose
TBD - created by archiving change add-forum-update-helper-bot. Update Purpose after archive.
## Requirements
### Requirement: Onboarding profile
The bot SHALL onboard each new user by collecting or defaulting business club,
full name, forum group name, methodology, report recipient username, file
retention preference, and the next forum date before exposing the main
preparation workflow. Every onboarding step except methodology SHALL provide a
Telegram-native skip action. The methodology step SHALL require an explicit
choice. The bot SHALL send reports to the report recipient only when that
Telegram username belongs to a user who has already started the bot.

#### Scenario: User completes onboarding
- **WHEN** a new user sends `/start` and answers or skips all onboarding prompts
- **THEN** the bot stores the profile and shows the main Telegram menu

#### Scenario: Existing user restarts onboarding
- **WHEN** an existing user sends `/start`
- **THEN** the bot starts the onboarding questions again
- **AND** each question with a previously stored value offers that value as a confirmation button
- **AND** the skip action clears the optional stored value for that question

#### Scenario: User skips identity fields
- **WHEN** the user skips business club, full name, and forum group
- **THEN** the bot stores empty values for those optional fields

#### Scenario: User selects methodology
- **WHEN** the user chooses `Классическая (YPO)` or `С личной стратегией (X-Competence)`
- **THEN** the selected methodology is stored and used for update preparation

#### Scenario: User tries to skip methodology
- **WHEN** the user reaches the methodology step
- **THEN** the bot SHALL NOT provide a skip action for this step

#### Scenario: Admin receives minimal new-user notification
- **WHEN** onboarding is completed and `ADMIN_CHAT_ID` is configured
- **THEN** the bot sends the admin only the user's full name and business club

### Requirement: Telegram-native menu
The bot SHALL provide persistent and inline Telegram buttons for core actions:
prepare update, set next forum date, run health check, personal cabinet, diary
mode, diary prompt editing, information submenu, and delete my data. The
information submenu SHALL contain bot info, build your own bot, find
psychologist, find mentor, and contact author actions.

#### Scenario: User opens menu
- **WHEN** an onboarded user sends `/menu`
- **THEN** the bot replies with a native keyboard and inline links/actions

#### Scenario: User opens a submenu
- **WHEN** a user opens an information, guide, diary, or profile submenu
- **THEN** the submenu includes a native "Назад" action to return to the previous menu or main menu

### Requirement: Message pacing
The bot SHALL answer immediately after user messages or button presses. When the
bot sends multiple messages in a row to the same chat, it SHALL wait two seconds
between bot messages.

#### Scenario: Bot sends consecutive messages
- **WHEN** a bot workflow needs to send two messages in a row to the same chat
- **THEN** the first message is sent without an artificial delay after the user's action
- **AND** the second bot message is sent after a two-second pause

### Requirement: Voice and audio transcription
The bot SHALL accept voice and audio messages, transcribe them through the
configured transcription provider, echo the transcript to the user, and pass the
transcribed text to the active conversation flow. The bot SHALL delete downloaded
audio immediately after transcription or failed transcription, regardless of the
user's file-retention setting.

#### Scenario: User answers by voice
- **WHEN** a user sends a Telegram voice message while a flow is active
- **THEN** the bot shows the transcript and records it as the answer for the
  current flow step

### Requirement: X-Competence update flow
The bot SHALL guide the user through all sections of the selected forum update
methodology. For `Классическая (YPO)`, the bot SHALL use the classic monthly
update format. For `С личной стратегией (X-Competence)`, the bot SHALL use the
X-Competence format.

#### Scenario: User advances through questions
- **WHEN** the user answers a questionnaire step
- **THEN** the bot shows a filled counter such as `3/50` and waits for the
  user to press "Далее" before asking the next question

#### Scenario: User completes update preparation
- **WHEN** the user answers all update-flow questions
- **THEN** the bot generates a Markdown forum update document and sends it to
  the user

### Requirement: Forum date reminders
The bot SHALL ask for the next forum date, store it, and use it for pre-forum
update reminders and next-morning post-forum health check prompts. The bot SHALL
accept full dates and short `D.MM` / `DD.MM` dates; short dates SHALL use the
current year unless that date has already passed, in which case they SHALL use
the next year.

#### Scenario: Pre-forum reminder is due
- **WHEN** the stored forum date is three days away
- **THEN** the bot starts the update flow, asks the first X-Competence question,
  and stores that the reminder was sent

#### Scenario: Post-forum health check is due
- **WHEN** the stored forum date was yesterday
- **THEN** the bot starts or offers the forum-group health check once for that
  forum date

### Requirement: Quarterly offsite reminder
The bot SHALL remind each active user every three months to plan a personal
strategy session outside the city and include practical venue-selection
criteria.

#### Scenario: Offsite reminder is due
- **WHEN** at least the configured offsite interval has passed since the last
  offsite reminder
- **THEN** the bot sends a strategy-session prompt and stores that the reminder
  was sent

### Requirement: Forum-group health report
The bot SHALL ask post-forum health questions and generate a concise report that
can be sent to the configured Telegram username when that user has already
started the bot.

#### Scenario: User completes health check
- **WHEN** the user answers all health-check questions
- **THEN** the bot sends a summary to the user and attempts to send it to the
  configured report recipient

### Requirement: Privacy deletion
The bot SHALL let a user delete their stored profile, flow state, retained files,
and reminder history from the server.

#### Scenario: User confirms deletion
- **WHEN** the user presses delete data and confirms
- **THEN** the bot removes that user's local records and retained files and
  confirms completion

### Requirement: Runtime logging and counters
The bot SHALL log key non-secret runtime events and maintain aggregate counters
for users and interactions.

#### Scenario: Admin requests stats
- **WHEN** an admin sends `/stats`
- **THEN** the bot reports total users and interaction counts without exposing
  secrets or full message bodies
