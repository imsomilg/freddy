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
- `today` or `what's on tomorrow` for goals plus calendar events
- anything else, like `move my evening check-in to 10pm` or `how did I do this week?`

Every morning and evening it checks in with your goals for the day. Sunday evening it sends a weekly summary: goals done vs set, streak and best day.

With Google Calendar connected it can also list, add, move and delete events and find free time, like `lunch with Raj tomorrow 1 to 2pm` or `find me 2 hours on friday`. The morning check-in and `/today` show the day's events next to your goals. Deletes and anything that would email guests need a yes from you first, and guests are never emailed unless you say so. For repeating events it only touches the one occurrence; change the whole series in Google Calendar.

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
| `WEEKLY_SUMMARY_DELAY` | Minutes after the Sunday evening check-in for the weekly summary. Default `30` |
| `GITHUB_REPO` | Optional. Like `you/freddy`, so the boot message can list what changed |
| `GITHUB_TOKEN` | Optional. Fine-grained token with Contents read on that repo. Only needed while it's private |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional. Desktop app OAuth client, see Google Calendar setup |
| `GOOGLE_REFRESH_TOKEN` | Optional. Printed by the auth script. Calendar stays off unless all three are set |
| `GOOGLE_CALENDAR_ID` | Defaults to `primary` |

## Boot message

Every time Freddy starts it posts a greeting in the channel. If the commit changed since the last boot it says what's new, otherwise it says it was just a restart.

It reads the commit from `SOURCE_COMMIT`, which Coolify passes in at build time (see Deploy, step 5). Without `GITHUB_REPO` it shows the short sha instead of the commit titles.

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

## Google Calendar setup

Optional. Freddy talks to your calendar as you, through an OAuth refresh token. It asks for two scopes: `calendar.events` and `calendar.freebusy`.

**Google Cloud console**
1. **Menu** > **IAM & Admin** > **Create a Project**. Give it a name and click **Create**.
2. Enable the Google Calendar API for that project: https://console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com
3. **Google Auth platform** > **Branding**, click **Get Started**:
   - App name and user support email, **Next**
   - Audience: **External**, **Next**
   - Contact email, **Next**
   - Agree to the User Data Policy, **Continue**, **Create**
4. **Data Access** > **Add or Remove Scopes**: add `.../auth/calendar.events` and `.../auth/calendar.freebusy`, **Save**.
5. **Audience**: under Publishing status click **Publish app**, so it's **In production**. In Testing, Google kills the refresh token after 7 days.
6. **Clients** > **Create Client**, Application type **Desktop app**, any name, **Create**. Copy the client id and secret right away, Google only shows the full secret once.

**Get the refresh token** (on your machine, needs Docker)
```bash
docker compose --profile dev build freddy-dev
docker compose --profile dev run --rm -p 127.0.0.1:8085:8085 freddy-dev python -m freddy.auth.google
```
Paste the client id and secret, open the url it prints, sign in and allow both permissions. The app isn't verified, so Google shows a warning first; it's your own app, so continue past it. The script prints `GOOGLE_REFRESH_TOKEN=...`.

Put the three values in `.env` (or Coolify) and restart. On boot Freddy logs `google.calendar_ready` with the calendar's timezone, and warns if it doesn't match `TIMEZONE`.

If the token ever stops working (revoked, or unused for 6 months), Freddy says so in the channel once. Run the script again and swap the token.

## Deploy

I run it on Coolify.

1. Create an app from this repo with the Dockerfile build pack and set **Ports Exposes** to `8080`.
2. Add a Postgres database and use its internal url as `DATABASE_URL`.
3. Give the app a domain so the laptop can reach `/bridge` and `/mcp/`:
   - At your DNS provider, point an `A` record for the domain at the Coolify server. Ports 80 and 443 need to reach the server for the certificate.
   - In Coolify, open the app, then **Domains**, then **Add domain**. Protocol `https`, your domain, port `8080`.
4. Set the env vars above. Generate `BRIDGE_TOKEN` and `MCP_TOKEN` (see Laptop bridge). `ANTHROPIC_API_KEY` is optional. Add `GITHUB_REPO` and `GITHUB_TOKEN` if you want commit titles in the boot message, and the `GOOGLE_*` vars for the calendar.
5. In **Advanced**, under **Build**, set **Source commit availability** to **Available during build**. That bakes the commit into the image so the boot message knows what changed.
6. Deploy, and redeploy after any domain change.

Keep it to one replica, Discord only allows one connection per bot.
