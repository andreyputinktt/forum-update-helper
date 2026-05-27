## MODIFIED Requirements

### Requirement: Onboarding profile
The bot SHALL onboard each new user by collecting or defaulting business club,
full name, forum group name, Telegram community chat for reports, file retention
preference, and the next forum date before exposing the main preparation
workflow. Every onboarding step SHALL provide a Telegram-native skip action.

#### Scenario: User completes onboarding
- **WHEN** a new user sends `/start` and answers or skips all onboarding prompts
- **THEN** the bot stores the profile and shows the main Telegram menu

#### Scenario: User skips identity fields
- **WHEN** the user skips business club, full name, and forum group
- **THEN** the bot stores business club `Другое`, a generated full name, and a
  generated forum group name

#### Scenario: Admin receives minimal new-user notification
- **WHEN** onboarding is completed and `ADMIN_CHAT_ID` is configured
- **THEN** the bot sends the admin only the user's full name and business club

### Requirement: Telegram-native menu
The bot SHALL provide persistent and inline Telegram buttons for core actions:
prepare update, set next forum date, run health check, personal cabinet, diary
mode, diary prompt editing, bot info, build your own bot, find psychologist,
find coach, contact author, show stats, and delete my data.

#### Scenario: User opens menu
- **WHEN** an onboarded user sends `/menu`
- **THEN** the bot replies with a native keyboard and inline links/actions
