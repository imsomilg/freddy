# Freddy

My personal assistant. It lives in a channel on my Discord server, keeps me on top of my daily goals and pings me with reminders. Simple stuff is handled by plain Python, and anything it can't parse goes to the Anthropic API.

## What it does right now

Just talk to it in plain English:

- `add goal: read 20 pages`
- `goals` or `what are my goals?`
- `done reading` or `done 12`
- `remind me in 20 min to stretch`
- `remind me at 6pm to call mom`
- `remind me every weekday at 7:30 to take vitamins`
- `reminders` and `cancel reminder 3`
- anything else, like `move my evening check-in to 10pm` or `how did I do this week?`

Every morning and evening it checks in with your goals for the day.

Google Calendar, slash commands and a weekly summary are coming next.

## How it works

- Messages first go through a fast path in `src/freddy/fastpath/`. Common requests are parsed with regex and handled instantly, no tokens spent.
- Anything else goes to the model with a small set of tools for goals and reminders.
- Reminders and check-ins live in Postgres. A loop checks for due ones every 30 seconds, so nothing is lost on restart.

## Run it locally

You need Docker.

```bash
cp .env.example .env   # fill it in
docker compose up --build
```

For live reload while editing:

```bash
docker compose watch
```

Tests, lint and types:

```bash
docker compose --profile dev run --rm freddy-dev pytest
docker compose --profile dev run --rm freddy-dev ruff check src tests
docker compose --profile dev run --rm freddy-dev mypy src
```

## Config

| Variable | What it is |
|---|---|
| `DISCORD_TOKEN` | Bot token |
| `DISCORD_GUILD_ID` | Your server id |
| `DISCORD_CHANNEL_ID` | The channel Freddy listens in |
| `OWNER_DISCORD_ID` | Your user id, the only person Freddy answers |
| `ANTHROPIC_API_KEY` | API key |
| `CLAUDE_MODEL` | Defaults to `claude-sonnet-5-5` |
| `DATABASE_URL` | Postgres url, `postgres://` works too |
| `TIMEZONE` | Like `Asia/Kolkata` |
| `MORNING_CHECKIN` / `EVENING_CHECKIN` | Defaults `08:00` and `21:00` |

## Discord bot setup

1. Developer Portal, create an app.
2. **Bot** page: reset and copy the token, turn on **Message Content Intent**.
3. **Installation** page: Guild Install, scopes `bot` and `applications.commands`, permissions View Channels, Send Messages, Read Message History, Embed Links.
4. Open the install link and add it to your server.
5. Turn on Developer Mode in Discord and copy the server, channel and your user id.

## Deploy

I run it on Coolify. Create an app from this repo with the Dockerfile build pack, add a Postgres database, set the env vars above and deploy. Keep it to one replica, Discord only allows one connection per bot. No domain needed, the health check runs inside the container.
