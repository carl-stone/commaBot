#!/usr/bin/env python3
"""
commaBot GitHub Webhook Listener

Receives GitHub App webhook events, validates signatures,
and forwards them to the commaBot channel adapter for processing.

Events handled: issues, issue_comment, pull_request, push, check_run,
check_suite, pull_request_review, pull_request_review_comment, label,
release, commit_comment, milestone, member, pull_request_review_thread,
sub_issues
"""

import hmac
import hashlib
import json
import os
import sys
import logging
from flask import Flask, request, abort

try:
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
except ImportError:
    from urllib2 import Request, urlopen, URLError, HTTPError

# ---------------------------------------------------------------------------
# Configuration (from environment variables)
# ---------------------------------------------------------------------------
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
CHANNEL_ADAPTER_URL = os.environ.get("SDK_RELAY_URL", "http://127.0.0.1:3000/relay")
PORT = int(os.environ.get("WEBHOOK_PORT", "8080"))

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
log = logging.getLogger("webhook-listener")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


def verify_signature(payload_body: bytes, signature_header: str) -> bool:
    """Verify that the payload was sent by GitHub by validating the
    X-Hub-Signature-256 header against the webhook secret."""
    if not WEBHOOK_SECRET:
        log.error("WEBHOOK_SECRET not configured - cannot verify signatures")
        return False
    if not signature_header:
        return False
    hash_algorithm, github_signature = signature_header.split("=", 1)
    if hash_algorithm != "sha256":
        log.warning("Unexpected hash algorithm: %s", hash_algorithm)
        return False
    computed = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        msg=payload_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(computed, github_signature)


def format_event(event_type: str, action: str, payload: dict) -> dict:
    """Format a GitHub webhook event as a concise message for commaBot.

    Returns a dict with:
      - message: str — the formatted event text
      - thread_id: str | None — the issue/PR number (for threadId in channel adapter)
    """
    sender = payload.get("sender", {}).get("login", "unknown")
    repo = payload.get("repository", {}).get("full_name", "unknown-repo")
    thread_id = None

    if event_type == "issues":
        issue = payload.get("issue", {})
        number = issue.get("number", "?")
        thread_id = str(number)
        title = issue.get("title", "?")
        url = issue.get("html_url", "")
        body_preview = (issue.get("body") or "")[:300]
        return {
            "message": (
                f"[GitHub] Issue {action} on {repo} #{number}: {title} | "
                f"By: {sender} | URL: {url} | Body: {body_preview}"
            ),
            "thread_id": thread_id,
        }

    elif event_type == "issue_comment":
        comment = payload.get("comment", {})
        issue = payload.get("issue", {})
        number = issue.get("number", "?")
        thread_id = str(number)
        title = issue.get("title", "?")
        is_pr = "pull_request" in issue
        kind = "PR" if is_pr else "issue"
        comment_id = comment.get("id", "")
        comment_body = (comment.get("body") or "")[:500]
        url = comment.get("html_url", "")
        return {
            "message": (
                f"[GitHub] Comment on {kind} {repo} #{number}: {title} | "
                f"By: {sender} | Comment ID: {comment_id} | URL: {url} | Comment: {comment_body}"
            ),
            "thread_id": thread_id,
        }

    elif event_type == "pull_request":
        pr = payload.get("pull_request", {})
        number = pr.get("number", "?")
        thread_id = str(number)
        title = pr.get("title", "?")
        url = pr.get("html_url", "")
        base = pr.get("base", {}).get("ref", "?")
        head = pr.get("head", {}).get("ref", "?")
        merged = pr.get("merged", False)
        if action == "closed" and merged:
            action = "merged"
        return {
            "message": (
                f"[GitHub] Pull request {action} on {repo} #{number}: {title} | "
                f"By: {sender} | {head} -> {base} | URL: {url}"
            ),
            "thread_id": thread_id,
        }

    elif event_type == "push":
        ref = payload.get("ref", "?")
        after = payload.get("after", "?")[:7]
        commits = payload.get("commits", [])
        num_commits = len(commits)
        compare_url = payload.get("compare", "")
        return {
            "message": (
                f"[GitHub] Push to {repo} {ref} | "
                f"By: {sender} | {num_commits} commit(s) | {after} | Compare: {compare_url}"
            ),
            "thread_id": None,
        }

    elif event_type == "check_run":
        check_run = payload.get("check_run", {})
        name = check_run.get("name", "?")
        status = check_run.get("status", "?")
        conclusion = check_run.get("conclusion", "?") or "in_progress"
        html_url = check_run.get("html_url", "")
        return {
            "message": (
                f"[GitHub] Check run {action} on {repo} | "
                f"Name: {name} | Status: {status} | Conclusion: {conclusion} | "
                f"By: {sender} | URL: {html_url}"
            ),
            "thread_id": None,
        }

    elif event_type == "check_suite":
        check_suite = payload.get("check_suite", {})
        status = check_suite.get("status", "?")
        conclusion = check_suite.get("conclusion", "?") or "in_progress"
        head_branch = check_suite.get("head_branch", "?")
        html_url = check_suite.get("html_url", "")
        return {
            "message": (
                f"[GitHub] Check suite {action} on {repo} | "
                f"Branch: {head_branch} | Status: {status} | Conclusion: {conclusion} | "
                f"By: {sender} | URL: {html_url}"
            ),
            "thread_id": None,
        }

    elif event_type == "pull_request_review":
        pr = payload.get("pull_request", {})
        review = payload.get("review", {})
        number = pr.get("number", "?")
        thread_id = str(number)
        title = pr.get("title", "?")
        state = review.get("state", "?")
        review_id = review.get("id", "")
        body = (review.get("body") or "")[:300]
        html_url = review.get("html_url", "")
        return {
            "message": (
                f"[GitHub] Review {state} on {repo} PR #{number}: {title} | "
                f"By: {sender} | Review ID: {review_id} | URL: {html_url} | Comment: {body}"
            ),
            "thread_id": thread_id,
        }

    elif event_type == "pull_request_review_comment":
        pr = payload.get("pull_request", {})
        comment = payload.get("comment", {})
        number = pr.get("number", "?")
        thread_id = str(number)
        title = pr.get("title", "?")
        comment_id = comment.get("id", "")
        body = (comment.get("body") or "")[:300]
        html_url = comment.get("html_url", "")
        return {
            "message": (
                f"[GitHub] Review comment on {repo} PR #{number}: {title} | "
                f"By: {sender} | Comment ID: {comment_id} | URL: {html_url} | Comment: {body}"
            ),
            "thread_id": thread_id,
        }

    elif event_type == "label":
        label = payload.get("label", {})
        name = label.get("name", "?")
        color = label.get("color", "?")
        return {
            "message": (
                f"[GitHub] Label {action} on {repo} | Label: {name} (#{color}) | By: {sender}"
            ),
            "thread_id": None,
        }

    elif event_type == "release":
        release = payload.get("release", {})
        tag = release.get("tag_name", "?")
        name = release.get("name", "?") or tag
        prerelease = release.get("prerelease", False)
        html_url = release.get("html_url", "")
        kind = "pre-release" if prerelease else "release"
        return {
            "message": (
                f"[GitHub] {kind.capitalize()} {action} on {repo} | "
                f"Tag: {tag} | Name: {name} | By: {sender} | URL: {html_url}"
            ),
            "thread_id": None,
        }

    elif event_type == "commit_comment":
        comment = payload.get("comment", {})
        comment_id = comment.get("id", "")
        body = (comment.get("body") or "")[:300]
        html_url = comment.get("html_url", "")
        commit_id = comment.get("commit_id", "?")[:7]
        return {
            "message": (
                f"[GitHub] Commit comment {action} on {repo} | "
                f"Commit: {commit_id} | By: {sender} | Comment ID: {comment_id} | URL: {html_url} | Comment: {body}"
            ),
            "thread_id": None,
        }

    elif event_type == "milestone":
        milestone = payload.get("milestone", {})
        title = milestone.get("title", "?")
        state = milestone.get("state", "?")
        html_url = milestone.get("html_url", "")
        return {
            "message": (
                f"[GitHub] Milestone {action} on {repo} | "
                f"Title: {title} | State: {state} | By: {sender} | URL: {html_url}"
            ),
            "thread_id": None,
        }

    elif event_type == "member":
        member = payload.get("member", {})
        login = member.get("login", "?")
        return {
            "message": (
                f"[GitHub] Member {action} on {repo} | "
                f"Member: {login} | By: {sender}"
            ),
            "thread_id": None,
        }

    elif event_type == "pull_request_review_thread":
        thread = payload.get("thread", {})
        pr = payload.get("pull_request", {})
        number = pr.get("number", "?")
        thread_id = str(number)
        title = pr.get("title", "?")
        thread_body = (thread.get("body") or "")[:300] if "body" in thread else ""
        return {
            "message": (
                f"[GitHub] Review thread {action} on {repo} PR #{number}: {title} | "
                f"By: {sender} | Thread: {thread_body}"
            ),
            "thread_id": thread_id,
        }

    elif event_type == "sub_issues" or event_type == "issue_dependencies":
        # sub_issues replaces issue_dependencies in newer GitHub API
        sub_issue = payload.get("sub_issue", payload.get("blocked_issue", {}))
        parent_issue = payload.get("parent_issue", payload.get("blocking_issue", {}))
        parent_number = parent_issue.get("number", "?")
        parent_title = parent_issue.get("title", "?")
        sub_number = sub_issue.get("number", "?")
        sub_title = sub_issue.get("title", "?")
        return {
            "message": (
                f"[GitHub] Sub-issue {action} on {repo} | "
                f"Parent: #{parent_number} {parent_title} | Sub: #{sub_number} {sub_title} | By: {sender}"
            ),
            "thread_id": str(parent_number) if parent_number != "?" else None,
        }

    else:
        # Generic handler for any other events
        return {
            "message": f"[GitHub] Event: {event_type} {action} on {repo} by {sender}",
            "thread_id": None,
        }


def send_to_channel_adapter(message: str, sender_id: str = "github", repo: str = "", thread_id: str = None) -> bool:
    """Send a formatted message to the commaBot channel adapter.

    The channel adapter feeds the message into the Letta Code listener queue,
    which processes it on the listener code path (with working transcript
    writing and reflection launching).
    """
    payload = {
        "message": message,
        "senderId": sender_id,
        "chatId": repo if repo else "carl-stone/comma",  # No channel prefix — channel is a separate field in route key
    }
    if thread_id:
        payload["threadId"] = thread_id
    data = json.dumps(payload).encode("utf-8")

    req = Request(
        CHANNEL_ADAPTER_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "commaBot-webhook/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read().decode("utf-8", errors="replace")[:500]
            if 200 <= status < 300:
                log.info("Forwarded to channel adapter (HTTP %d): %s", status, body)
                return True
            else:
                log.error("Channel adapter returned HTTP %d: %s", status, body)
                return False
    except HTTPError as e:
        log.error("Channel adapter HTTP error: %d %s", e.code, e.reason)
        return False
    except URLError as e:
        log.error("Channel adapter connection error: %s", e.reason)
        return False
    except Exception as e:
        log.error("Unexpected error forwarding to channel adapter: %s", e)
        return False


@app.route("/webhook", methods=["POST"])
def github_webhook():
    """Handle incoming GitHub webhook POST requests."""
    # 1. Validate signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    payload_body = request.get_data()
    if not verify_signature(payload_body, signature):
        log.warning("Invalid signature - rejecting request")
        abort(401)

    # 2. Parse event type and action
    event_type = request.headers.get("X-GitHub-Event", "")
    payload = request.get_json(silent=True)
    if not payload:
        log.warning("Could not parse JSON payload")
        abort(400)

    action = payload.get("action", "")
    sender = payload.get("sender", {}).get("login", "")
    log.info("Received: %s %s (by %s)", event_type, action, sender)

    # 3. Skip events from commabot[bot] to prevent feedback loops
    if sender == "commabot[bot]":
        log.info("Skipping self-generated event from commabot[bot]")
        return "OK (self-filtered)", 200

    # 4. Forward to channel adapter (fire-and-forget)
    formatted = format_event(event_type, action, payload)
    message = formatted["message"]
    thread_id = formatted.get("thread_id")
    log.info("Formatted message (%d chars): %s", len(message), message[:200])

    repo = payload.get("repository", {}).get("full_name", "")
    success = send_to_channel_adapter(message, sender_id=sender, repo=repo, thread_id=thread_id)
    if success:
        return "OK", 200
    else:
        log.error("Failed to forward event to channel adapter")
        return "Forwarding failed", 502


@app.route("/health", methods=["GET"])
def health():
    """Simple health check endpoint."""
    return "OK", 200


if __name__ == "__main__":
    log.info("Starting webhook listener on 0.0.0.0:%d", PORT)
    log.info("CHANNEL_ADAPTER_URL=%s", CHANNEL_ADAPTER_URL)
    log.info("WEBHOOK_SECRET=%s", "***configured***" if WEBHOOK_SECRET else "NOT SET")
    app.run(host="0.0.0.0", port=PORT)
