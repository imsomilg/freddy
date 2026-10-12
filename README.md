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

Slash commands for the quick stuff: `/goals`, `/today`, `/done`, `/remind`, `/reminders`, `/checkin`.

Every morning and evening it checks in with your goals for the day. Sunday evening it sends a weekly summary: goals done vs set, streak and best day.

With Google Calendar connected it can also list, add, move and delete events and find free time, like `lunch with Raj tomorrow 1 to 2pm` or `find me 2 hours on friday`. The morning check-in and `/today` show the day's events next to your goals. Deletes and anything that would email guests need a yes from you first, and guests are never emailed unless you say so. For repeating events it only touches the one occurrence; change the whole series in Google Calendar. It also pings you 10 minutes before each timed event. It checks the calendar every 5 minutes, so moved events move their ping and cancelled ones drop it. No pings for all-day events or ones you declined.

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
| `BRIDGE_TIMEOUT` | Seconds to wait for the laptop before falling back. Default `150` |
| `CLAUDE_MODEL` | Defaults to `claude-sonnet-5-5` |
| `CLAUDE_EFFORT` | Defaults to `low` |
| `CLAUDE_MAX_TOOL_ROUNDS` | Max tool rounds per message. Default `8` |
| `CLAUDE_MAX_TOKENS` | Max tokens per reply. Default `4096` |
| `DATABASE_URL` | Postgres url, `postgres://` works too |
| `TIMEZONE` | Like `Asia/Kolkata` |
| `MORNING_CHECKIN` / `EVENING_CHECKIN` | Defaults `08:00` and `21:00` |
| `WEEKLY_SUMMARY_DELAY` | Minutes after the Sunday evening check-in for the weekly summary. Default `30` |
| `GITHUB_REPO` | Optional. Like `you/freddy`, so the boot message can list what changed |
| `GITHUB_TOKEN` | Optional. Fine-grained token with Contents read on that repo. Only needed while it's private |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional. Desktop app OAuth client, see Google Calendar setup |
| `GOOGLE_REFRESH_TOKEN` | Optional. Printed by the auth script. Calendar stays off unless all three are set |
| `GOOGLE_CALENDAR_ID` | Defaults to `primary` |
| `EVENT_PING_MINUTES` | How many minutes before an event Freddy pings you. Default `10` |
| `LOG_LEVEL` | Defaults to `INFO` |
| `LOG_JSON` | `true` for JSON logs. Default `false` |
| `HEALTH_PORT` | Port for `/healthz`, `/bridge` and `/mcp/`. Default `8080`. Leave it, the Dockerfile health check expects 8080 |

`SOURCE_COMMIT` is set by Coolify at build time, you don't set it yourself.

## Self-hosting

It's built for one person: it only answers its owner, in one channel. I run it on [Coolify](https://coolify.io), so the steps below are for Coolify. You need a Discord server you own, a Claude API key and a Coolify server. Google Calendar and the laptop bridge are optional.

UIs change. These steps were checked against the official docs in October 2026, links at the bottom.

### 1. Discord bot

1. Open the [Discord Developer Portal](https://discord.com/developers/applications), click **Create App**, give it a name and click **Create**.
2. **Bot** page:
   - Under **Token**, click **Reset Token** and copy it. You only see it once. That's `DISCORD_TOKEN`.
   - Untick **Public Bot**, so only you can add it to servers.
   - Under **Privileged Gateway Intents**, turn on **Message Content Intent**. Freddy reads your messages, so it needs this. No verification needed under 100 servers.
3. **Installation** page:
   - **Installation Contexts:** keep **Guild Install**, untick **User Install**.
   - **Install Link:** **Discord Provided Link**.
   - **Default Install Settings**, Guild Install: scopes `applications.commands` and `bot`. Permissions **View Channels**, **Send Messages** and **Read Message History** (Freddy replies to your message, and Discord needs that permission for replies).
4. Open the install link, pick **Add to server**, choose your server and authorize.
5. Make a channel for it, like `#freddy`. If it's private, add the bot to it.
6. In Discord: **User Settings** > **Advanced** > turn on **Developer Mode**. Then:
   - right-click the server icon > **Copy Server ID**, that's `DISCORD_GUILD_ID`
   - right-click the channel > **Copy Channel ID**, that's `DISCORD_CHANNEL_ID`
   - right-click yourself > **Copy User ID**, that's `OWNER_DISCORD_ID`

### 2. Claude API key

1. Sign in at the [Claude Console](https://platform.claude.com), go to **Settings** > **API keys**, click **Create key** and copy it. It's shown only once. That's `ANTHROPIC_API_KEY`.
2. Optional but smart: **Settings** > **Billing**, under **Spend limits** click **Set limit** and pick a monthly cap.

### 3. Postgres on Coolify

1. In your project and environment, click **New Resource**, then **PostgreSQL** under **Databases**.
2. Pick the server and network, then the image. Coolify defaults to PostgreSQL 18; pick 17, that's what Freddy is tested on.
3. Click **Start** and wait until it's healthy.
4. Copy **Postgres URL (internal)**. That's `DATABASE_URL`. The `postgres://` form is fine, Freddy converts it.

### 4. The app on Coolify

1. Fork this repo.
2. In the same project and environment, click **+ New**:
   - public fork: **Public Repository**, paste your fork's url, **Check Repository**
   - private fork: set up a GitHub App source first (**Sources** > **New source** > **GitHub App**), then **Private Repository (with GitHub App)**
3. Set **Build pack** to **Dockerfile**, **Base Directory** `/`, **Dockerfile Location** `/Dockerfile`, **Ports Exposes** `8080`. Click **Continue**.
4. Open **Environment Variables**, switch to **Developer view**, paste `.env.example`, fill it in and save (see Config). Required: the four Discord values and `DATABASE_URL`. Also set `ANTHROPIC_API_KEY` and your `TIMEZONE`.
5. Click **Deploy** and watch the logs. You should see the Alembic migrations, `freddy.starting` and `discord.ready`, and Freddy posts a greeting in the channel.

Keep it to one replica, Discord only allows one connection per bot. No domain needed unless you use the laptop bridge.

### 5. Smoke test

In your channel:

1. `add goal: test freddy`, then `goals`
2. `remind me in 2 min to stretch`, and wait for the ping
3. `how am I doing this week?` goes to Claude

## Boot message

Every time Freddy starts it posts a greeting in the channel. If the commit changed since the last boot it says what's new, otherwise it says it was just a restart.

It reads the commit from `SOURCE_COMMIT`. In Coolify, open the app's **Advanced** > **Build** and set **Source commit availability** to **Available during build**, so it's baked into the image. Without `GITHUB_REPO` it shows the short sha instead of the commit titles.

## Laptop bridge

Lets Freddy use Claude Code on my laptop (my own subscription) whenever the laptop is on.

```
Discord -> Freddy (VPS) -> no fast path match -> laptop bridge -> claude -p -> Freddy's tools (MCP) -> reply
```

The laptop connects out to Freddy over a WebSocket, so there's nothing to open on the router. Claude on the laptop gets no shell or file tools, only Freddy's goal, reminder and calendar tools.

**On the server**
1. Give the app a domain. At your DNS provider, point an `A` record at the Coolify server (ports 80 and 443 need to reach it). In Coolify, open the app, **Domains** > **Add domain**: protocol `https`, your domain, port `8080`. Redeploy after.
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

**Personal use only.** Use your own subscription on your own machine, never set it up for anyone else. It runs the official, unmodified Claude Code, but Anthropic's terms don't clearly allow or ban this, so keep it low volume and read [Claude Code's legal and compliance page](https://code.claude.com/docs/en/legal-and-compliance) yourself. If in doubt, skip it and use `ANTHROPIC_API_KEY`.

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

## Docs I checked

- Discord: [getting started](https://docs.discord.com/developers/quick-start/getting-started), [gateway intents](https://docs.discord.com/developers/topics/gateway), [message permissions](https://docs.discord.com/developers/resources/message), [finding ids](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID)
- Claude: [API keys](https://platform.claude.com/docs/en/get-api-key), [spend limits](https://platform.claude.com/docs/en/api/rate-limits), [Claude Code legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
- Coolify: [Dockerfile build pack](https://coolify.io/docs/applications/build-packs/dockerfile), [PostgreSQL](https://coolify.io/docs/databases/postgresql), [environment variables](https://coolify.io/docs/knowledge-base/environment-variables), [domains](https://coolify.io/docs/knowledge-base/domains), [GitHub App](https://coolify.io/docs/applications/sources/github/app), [public repository](https://coolify.io/docs/applications/sources/github/public-repository)
- Google: [Calendar Python quickstart](https://developers.google.com/workspace/calendar/api/quickstart/python), [create a project](https://developers.google.com/workspace/guides/create-project), [OAuth consent](https://developers.google.com/workspace/guides/configure-oauth-consent), [refresh token expiry](https://developers.google.com/identity/protocols/oauth2), [OAuth clients](https://support.google.com/cloud/answer/15549257)

## License

MIT, see [LICENSE](LICENSE).
