"""One-off Google OAuth for Freddy. Prints a refresh token for GOOGLE_REFRESH_TOKEN.

docker compose --profile dev run --rm -p 127.0.0.1:8085:8085 freddy-dev python -m freddy.auth.google
"""

import getpass
import os

from google_auth_oauthlib.flow import InstalledAppFlow

from freddy.integrations.google.auth import SCOPES

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
PORT = 8085


def main() -> None:
    client_id = os.environ.get("GOOGLE_CLIENT_ID") or input("GOOGLE_CLIENT_ID: ").strip()
    client_secret = (
        os.environ.get("GOOGLE_CLIENT_SECRET") or getpass.getpass("GOOGLE_CLIENT_SECRET: ").strip()
    )
    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": AUTH_URI,
                "token_uri": TOKEN_URI,
            }
        },
        scopes=list(SCOPES),
    )
    creds = flow.run_local_server(
        host="127.0.0.1",
        bind_addr="0.0.0.0",
        port=PORT,
        open_browser=False,
        authorization_prompt_message="Open this in your browser:\n{url}\n",
    )

    granted = creds.granted_scopes or []
    if isinstance(granted, str):
        granted = granted.split()
    missing = [s for s in SCOPES if s not in granted]
    if missing:
        print("Missing scopes, tick every box on the consent screen and run it again:")
        print("\n".join(missing))
        raise SystemExit(1)
    if not creds.refresh_token:
        print("Google didn't return a refresh token. Run it again.")
        raise SystemExit(1)
    print(f"\nGOOGLE_REFRESH_TOKEN={creds.refresh_token}")


if __name__ == "__main__":
    main()
