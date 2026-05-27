## ADDED Requirements

### Requirement: Onboarding profile
The bot SHALL onboard each new user by collecting business club, full name,
forum group name, Telegram community chat for reports, and file retention
preference, then SHALL ask for the next forum date before exposing the main
preparation workflow.

#### Scenario: User completes onboarding
- **WHEN** a new user sends `/start` and answers all onboarding prompts
- **THEN** the bot stores the profile and shows the main Telegram menu

#### Scenario: Admin receives minimal new-user notification
- **WHEN** onboarding is completed and `ADMIN_CHAT_ID` is configured
- **THEN** the bot sends the admin only the user's full name and business club

### Requirement: Telegram-native menu
The bot SHALL provide persistent and inline Telegram buttons for core actions:
prepare update, set next forum date, run health check, about bot, find
psychologist, find coach, contact author, show stats, and delete my data.

#### Scenario: User opens menu
- **WHEN** an onboarded user sends `/menu`
- **THEN** the bot replies with a native keyboard and inline links/actions

### Requirement: Voice and audio transcription
The bot SHALL accept voice and audio messages, transcribe them through the
configured transcription provider, echo the transcript to the user, and pass the
transcribed text to the active conversation flow.

#### Scenario: User answers by voice
- **WHEN** a user sends a Telegram voice message while a flow is active
- **THEN** the bot shows the transcript and records it as the answer for the
  current flow step

### Requirement: X-Competence update flow
The bot SHALL guide the user through all sections of the X-Competence forum
update format: three-sphere ratings, retrospective, next-period perspective,
main request, annual-goal connection, and meeting action plan.

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
update reminders and next-morning post-forum health check prompts.

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
can be sent to the configured Telegram community chat.

#### Scenario: User completes health check
- **WHEN** the user answers all health-check questions
- **THEN** the bot sends a summary to the user and attempts to send it to the
  configured community chat

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
