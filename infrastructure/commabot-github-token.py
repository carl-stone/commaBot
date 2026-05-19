#!/usr/bin/env python3
"""
Generate a GitHub App installation access token for commaBot.

Usage:
    eval $(python3 commabot-github-token.py)
    # Sets GH_TOKEN in the shell environment

Or:
    GH_TOKEN=$(python3 commabot-github-token.py --token)
    # Just prints the token

Token expires after 1 hour. Re-run to refresh.
"""

import jwt
import json
import os
import sys
import time
from urllib.request import Request, urlopen

APP_ID = os.environ.get("GITHUB_APP_ID", "")
INSTALLATION_ID = os.environ.get("GITHUB_INSTALLATION_ID", "")
PRIVATE_KEY_PATH = os.environ.get(
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "/app/commabot.private-key.pem"
)


def get_installation_token():
    # Generate JWT from App private key
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": APP_ID}
    with open(PRIVATE_KEY_PATH, "r") as f:
        key = f.read()
    jwt_token = jwt.encode(payload, key, algorithm="RS256")

    # Exchange JWT for installation access token
    req = Request(
        f"https://api.github.com/app/installations/{INSTALLATION_ID}/access_tokens",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    with urlopen(req) as resp:
        data = json.loads(resp.read())
        return data["token"], data.get("expires_at", "unknown")


if __name__ == "__main__":
    token, expires = get_installation_token()
    if "--token" in sys.argv:
        print(token)
    else:
        print(f"export GH_TOKEN={token}")
        print(f"# Token expires at: {expires}", file=sys.stderr)
