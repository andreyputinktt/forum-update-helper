## MODIFIED Requirements

### Requirement: Telegram-native menu
The bot SHALL provide persistent and inline Telegram buttons for core actions:
prepare update, set next forum date, run health check, diary mode, diary prompt
editing, bot info, build your own bot, find psychologist, find coach, contact
author, show stats, and delete my data.

#### Scenario: User opens menu
- **WHEN** an onboarded user sends `/menu`
- **THEN** the bot replies with a native keyboard and inline links/actions

### Requirement: Runtime logging and counters
The bot SHALL log key non-secret runtime events and maintain aggregate counters
for users and interactions.

#### Scenario: Admin requests stats
- **WHEN** an admin sends `/stats`
- **THEN** the bot reports total users and interaction counts without exposing
  secrets or full message bodies
