#!/usr/bin/env python3
"""
commaBot Startup Recovery Script

Queries the GitHub App delivery API for failed webhook deliveries
(within a 7-day window) and redelivers them.

This runs as ExecStartPre in the webhook listener systemd service,
so it catches up on missed events before the listener comes online.

Authentication: Uses the GitHub App's private key to generate a JWT,
which is required for /app/hook/deliveries endpoints.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
except ImportError:
    from urllib2 import Request, urlopen, URLError, HTTPError

# ---------------------------------------------------------------------------
# Configuration (from environment variables)
# ---------------------------------------------------------------------------
GITHUB_APP_ID = os.environ.get("GITHUB_APP_ID", "")
GITHUB_APP_PRIVATE_KEY_PATH = os.environ.get(
    "GITHUB_APP_PRIVATE_KEY_PATH",
    "/app/commabot.private-key.pem",
)

# How far back to look for failed deliveries
RECOVERY_WINDOW_DAYS = 7
# Max deliveries to redeliver per run (prevents feedback loops and timeouts)
MAX_REDELIVERIES = 10

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("recovery")


# ---------------------------------------------------------------------------
# JWT generation for GitHub App authentication
# ---------------------------------------------------------------------------
def generate_jwt(app_id: str, private_key_path: str) -> str:
    """Generate a JWT for GitHub App authentication.

    Uses the RS256 algorithm. The JWT is valid for 10 minutes,
    issued 60 seconds in the past to account for clock drift.
    """
    try:
        import jwt  # PyJWT
    except ImportError:
        # Fallback: manual JWT construction without PyJWT
        log.warning("PyJWT not installed, attempting manual JWT construction")
        return _generate_jwt_manual(app_id, private_key_path)

    now = int(time.time())
    payload = {
        "iat": now - 60,       # Issued at (60s in the past for clock drift)
        "exp": now + (10 * 60),  # Expires at (10 minutes from now)
        "iss": app_id,          # Issuer = GitHub App ID
    }

    with open(private_key_path, "r") as f:
        private_key = f.read()

    token = jwt.encode(payload, private_key, algorithm="RS256")
    return token


def _generate_jwt_manual(app_id: str, private_key_path: str) -> str:
    """Manual JWT construction without PyJWT dependency.

    Builds the JWT header and payload, signs with RSA-SHA256,
    and base64url-encodes the result.
    """
    import base64
    import hashlib
    from cryptography.hazmat.primitives import serialization, hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")

    header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload_data = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": app_id,
    }
    payload = b64url(json.dumps(payload_data).encode())

    signing_input = f"{header}.{payload}".encode()

    with open(private_key_path, "rb") as f:
        private_key = serialization.load_pem_private_key(f.read(), password=None)

    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    sig_b64 = b64url(signature)

    return f"{header}.{payload}.{sig_b64}"


# ---------------------------------------------------------------------------
# GitHub API helpers
# ---------------------------------------------------------------------------
def github_request(url: str, jwt_token: str, method: str = "GET") -> dict:
    """Make an authenticated request to the GitHub API."""
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method=method,
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        log.error("GitHub API error: %d %s for %s", e.code, e.reason, url)
        return {}
    except Exception as e:
        log.error("GitHub API request failed: %s for %s", e, url)
        return {}


def get_failed_deliveries(jwt_token: str) -> list:
    """Query the GitHub App delivery API for failed deliveries
    within the recovery window."""
    url = "https://api.github.com/app/hook/deliveries?per_page=100"
    deliveries = github_request(url, jwt_token)

    if not deliveries:
        log.info("No deliveries found or API error")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=RECOVERY_WINDOW_DAYS)
    failed = []

    for delivery in deliveries:
        # Status can be: "succeeded", "failed", "not_delivered", etc.
        status = delivery.get("status", "")
        delivered_at = delivery.get("delivered_at", "")

        if status == "succeeded":
            continue

        # Parse the timestamp
        try:
            dt = datetime.fromisoformat(delivered_at.replace("Z", "+00:00"))
            if dt < cutoff:
                continue
        except (ValueError, AttributeError):
            log.warning("Could not parse delivery timestamp: %s", delivered_at)
            # Include it anyway — better to over-recover than under-recover

        failed.append(delivery)

    log.info(
        "Found %d failed/undelivered deliveries in last %d days",
        len(failed),
        RECOVERY_WINDOW_DAYS,
    )
    return failed


def redeliver(delivery_id: str, jwt_token: str) -> bool:
    """Request redelivery of a specific failed delivery."""
    url = f"https://api.github.com/app/hook/deliveries/{delivery_id}/attempts"
    req = Request(
        url,
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            if 200 <= resp.status < 300:
                log.info("Redelivery requested for delivery %s", delivery_id)
                return True
            else:
                log.error("Redelivery failed for %s: HTTP %d", delivery_id, resp.status)
                return False
    except HTTPError as e:
        log.error("Redelivery HTTP error for %s: %d %s", delivery_id, e.code, e.reason)
        return False
    except Exception as e:
        log.error("Redelivery error for %s: %s", delivery_id, e)
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    log.info("=== commaBot Recovery Script Starting ===")

    # Validate configuration
    if not GITHUB_APP_ID:
        log.error("GITHUB_APP_ID not set — cannot authenticate with GitHub")
        sys.exit(1)

    if not os.path.exists(GITHUB_APP_PRIVATE_KEY_PATH):
        log.error(
            "GitHub App private key not found at: %s",
            GITHUB_APP_PRIVATE_KEY_PATH,
        )
        sys.exit(1)

    # Generate JWT
    try:
        jwt_token = generate_jwt(GITHUB_APP_ID, GITHUB_APP_PRIVATE_KEY_PATH)
        log.info("Generated JWT for GitHub App %s", GITHUB_APP_ID)
    except Exception as e:
        log.error("Failed to generate JWT: %s", e)
        sys.exit(1)

    # Get failed deliveries
    failed = get_failed_deliveries(jwt_token)
    if not failed:
        log.info("No failed deliveries to recover — exiting cleanly")
        sys.exit(0)

    # Redeliver each failed delivery (capped to prevent feedback loops)
    recovered = 0
    for delivery in failed[:MAX_REDELIVERIES]:
        delivery_id = delivery.get("id", "?")
        log.info(
            "Attempting redelivery for %s (status: %s, event: %s)",
            delivery_id,
            delivery.get("status", "?"),
            delivery.get("event", "?"),
        )
        if redeliver(delivery_id, jwt_token):
            recovered += 1
        time.sleep(1)  # Rate limit courtesy

    if len(failed) > MAX_REDELIVERIES:
        log.warning(
            "Capped at %d redeliveries (%d total failed) — remaining will be retried next cycle",
            MAX_REDELIVERIES, len(failed),
        )

    log.info("Recovery complete: %d/%d deliveries redelivered", recovered, len(failed))


if __name__ == "__main__":
    main()
