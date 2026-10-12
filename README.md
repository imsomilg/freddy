# Freddy

My personal assistant. It lives in a channel on my Discord server, keeps me on top of my daily goals and pings me with reminders. Simple stuff is handled by plain Python. Anything it can't parse goes to Claude Code on my laptop when it's on, then to the Anthropic API if `ANTHROPIC_API_KEY` is set, otherwise Freddy says its brain is offline.

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
- Anything else goes to Claude, in this order:
  1. **Laptop bridge**, if my laptop is on: Claude Code on my laptop answers, using Freddy's tools over MCP.
  2. **API**, if `ANTHROPIC_API_KEY` is set.
  3. Otherwise Freddy says its brain is offline and lists what still works.
- Reminders and check-ins live in Postgres. A loop checks for due ones every 30 seconds, so nothing is lost on restart. They never need Claude.

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
docker compose --profile dev run --rm freddy-dev ruff format --check src tests scripts migrations
docker compose --profile dev run --rm freddy-dev ruff check src tests scripts migrations
docker compose --profile dev run --rm freddy-dev mypy src
docker compose --profile dev run --rm freddy-dev sh -c "alembic upgrade head && alembic check"
```

CI runs the same checks on every PR, plus a build of the runtime image.

## Config

| Variable | What it is |
|---|---|
| `DISCORD_TOKEN` | Bot token |
| `DISCORD_GUILD_ID` | Your server id |
| `DISCORD_CHANNEL_ID` | The channel Freddy listens in |
| `OWNER_DISCORD_ID` | Your user id, the only person Freddy answers |
| `ANTHROPIC_API_KEY` | Optional. API key, used when the laptop bridge is offline |
| `BRIDGE_TOKEN` | Optional. Shared secret for the laptop bridge |
| `MCP_TOKEN` | Optional. Secret the laptop uses to call Freddy's tools |
| `CLAUDE_MODEL` | Defaults to `claude-sonnet-5-5` |
| `DATABASE_URL` | Postgres url, `postgres://` works too |
| `TIMEZONE` | Like `Asia/Kolkata` |
| `MORNING_CHECKIN` / `EVENING_CHECKIN` | Defaults `08:00` and `21:00` |

## Laptop bridge

Lets Freddy use Claude Code on my laptop (my own subscription) whenever the laptop is on.

```
Discord -> Freddy (VPS) -> no fast path match -> laptop bridge -> claude -p -> Freddy's tools (MCP) -> reply
```

The laptop connects out to Freddy over a WebSocket, so there's nothing to open on the router. Claude on the laptop gets no shell or file tools, only Freddy's goal and reminder tools.

**On the server**
1. Give the app a domain in Coolify, for example `https://freddy.example.com` (port 8080).
2. Generate two secrets: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`
3. Set them as `BRIDGE_TOKEN` and `MCP_TOKEN`, then redeploy.

**On the laptop** (needs Claude Code installed and logged in, plus Python 3.11+)
```bash
./bridge/install.sh
# fill in ~/.config/freddy-bridge.env (FREDDY_URL, both tokens, CLAUDE_MODEL)
systemctl --user daemon-reload && systemctl --user enable --now freddy-bridge
journalctl --user -u freddy-bridge -f
```

`/healthz` shows `"bridge": "online"` when the laptop is connected. If the laptop sleeps, Freddy notices within about 45 seconds and falls back.

This runs the official, unmodified Claude Code just for me. Anthropic's terms don't clearly allow or ban this, so keep it personal and low volume.

## Discord bot setup

1. Developer Portal, create an app.
2. **Bot** page: reset and copy the token, turn on **Message Content Intent**.
3. **Installation** page: Guild Install, scopes `bot` and `applications.commands`, permissions View Channels, Send Messages, Read Message History, Embed Links.
4. Open the install link and add it to your server.
5. Turn on Developer Mode in Discord and copy the server, channel and your user id.

## Deploy

I run it on Coolify.

1. Create an app from this repo with the Dockerfile build pack and set **Ports Exposes** to `8080`.
2. Add a Postgres database and use its internal url as `DATABASE_URL`.
3. Give the app a domain so the laptop can reach `/bridge` and `/mcp/`:
   - At your DNS provider, point an `A` record for the domain at the Coolify server. Ports 80 and 443 need to reach the server for the certificate.
   - In Coolify, open the app, then **Domains**, then **Add domain**. Protocol `https`, your domain, port `8080`.
4. Set the env vars above. Generate `BRIDGE_TOKEN` and `MCP_TOKEN` (see Laptop bridge). `ANTHROPIC_API_KEY` is optional.
5. Deploy, and redeploy after any domain change.

Keep it to one replica, Discord only allows one connection per bot.
