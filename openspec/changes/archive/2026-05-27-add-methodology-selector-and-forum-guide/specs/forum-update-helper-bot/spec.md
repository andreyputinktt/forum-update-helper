## MODIFIED Requirements

### Requirement: Onboarding profile
The bot SHALL onboard each new user by collecting or defaulting business club,
full name, forum group name, methodology, Telegram community chat for reports,
file retention preference, and the next forum date before exposing the main
preparation workflow. Every onboarding step SHALL provide a Telegram-native skip
action.

#### Scenario: User completes onboarding
- **WHEN** a new user sends `/start` and answers or skips all onboarding prompts
- **THEN** the bot stores the profile and shows the main Telegram menu

#### Scenario: User skips identity fields
- **WHEN** the user skips business club, full name, and forum group
- **THEN** the bot stores business club `Другое`, a generated full name, and a
  generated forum group name

#### Scenario: User selects methodology
- **WHEN** the user chooses `YPO` or `Классическая`
- **THEN** the selected methodology is stored and used for update preparation

#### Scenario: Admin receives minimal new-user notification
- **WHEN** onboarding is completed and `ADMIN_CHAT_ID` is configured
- **THEN** the bot sends the admin only the user's full name and business club

### Requirement: X-Competence update flow
The bot SHALL guide the user through all sections of the selected forum update
methodology. For `YPO`, the bot SHALL use the X-Competence format. For
`Классическая`, the bot SHALL use the classic monthly update format.

#### Scenario: User advances through questions
- **WHEN** the user answers a questionnaire step
- **THEN** the bot shows a filled counter such as `3/50` and waits for the
  user to press "Далее" before asking the next question

#### Scenario: User completes update preparation
- **WHEN** the user answers all update-flow questions
- **THEN** the bot generates a Markdown forum update document and sends it to
  the user

