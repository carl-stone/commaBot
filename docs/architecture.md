# Webhook Pipeline Architecture

How commaBot receives GitHub events and turns them into agent actions.

## Overview

The pipeline gives an LLM agent autonomous, real-time awareness of GitHub activity on a repository. When something happens on GitHub — an issue is opened, a PR is reviewed, a CI check fails — the agent wakes up, investigates, and responds. No human needs to trigger it.

```
GitHub webhook → Tailscale Funnel (port 8080) → Flask webhook listener
  → POST localhost:3000/relay → GitHub channel adapter (inside letta server)
    → adapter.onMessage() → listener queue → agent processes with full tools
    → agent uses gh CLI (Bash) to interact with GitHub (NOT MessageChannel)
```

## Components

### 1. GitHub App

A GitHub App provides the bot's identity and permissions. The app is installed on the target repository and subscribes to webhook events. Key properties:

- **Identity**: The bot operates as `commabot[bot]` on GitHub — its commits, comments, and PRs are attributed separately from the human maintainer.
- **Permissions**: Issues, PRs, checks, contents, metadata (read/write as needed). The app does NOT have Projects v2 write access (a known GitHub limitation for App installation tokens).
- **Webhook secret**: Used by the Flask listener to verify that incoming payloads are actually from GitHub (HMAC-SHA256 signature verification).
- **Private key**: Used to generate JWTs for GitHub App authentication (installation access tokens for the `gh` CLI, and for the recovery script's API calls).

### 2. Flask Webhook Listener (`webhook-listener.py`)

A lightweight Flask app that receives GitHub webhook POSTs and forwards them to the channel adapter. Responsibilities:

- **Signature verification**: Validates the `X-Hub-Signature-256` header against the shared webhook secret. Rejects unauthenticated requests with 401.
- **Event formatting**: Parses the raw GitHub payload into a concise, human-readable message prefixed with `[GitHub]`. Each event type (issues, PRs, comments, checks, etc.) has a dedicated formatter that extracts the relevant fields.
- **Self-filtering**: Skips events where the sender is `commabot[bot]` to prevent feedback loops (the agent's own GitHub actions would trigger it again). Notably, the agent implemented this itself — Carl and the agent had discussed the principle that the agent shouldn't respond to its own events, but when the agent started receiving noisy notifications from its own GitHub activity, it recognized the problem, decided to add the filter to the Flask listener, and made the change without being asked. This is a concrete example of the agent identifying a problem and solving it autonomously rather than just following instructions.
- **Fire-and-forget**: Returns 200 to GitHub immediately after the channel adapter accepts the message. The agent processes the event asynchronously.

The listener handles 15 event types: issues, issue_comment, pull_request, pull_request_review, pull_request_review_comment, pull_request_review_thread, push, check_run, check_suite, commit_comment, label, milestone, member, release, sub_issues.

### 3. Channel Adapter (`channels/github/plugin.mjs`)

A Letta Code channel plugin that bridges the Flask listener into the agent's conversation system. The adapter:

- **Listens on port 3000** for POST requests from the Flask listener
- **Feeds messages into the listener queue** via `adapter.onMessage()`, which puts the agent on the listener code path (with working transcript writing and reflection launching)
- **One-way inbound**: The agent does NOT reply through the channel adapter. It uses the `gh` CLI (via the Bash tool) to interact with GitHub directly. The `MessageChannel` tool is used only for status receipts — one-line summaries of work already completed.
- **Route-based dispatch**: Messages are routed to a specific agent and conversation based on the `chatId` in the request (mapped to a route in `routing.yaml`).

### 4. Letta Code Server

The `letta server --channels github` process runs inside the Docker container. It:

- Loads the channel adapter plugin from `~/.letta/channels/github/`
- Connects to Letta Cloud for agent memory and conversation management
- Runs the agent on the listener code path (which has working transcript writing and reflection launching, unlike the SDK headless path — see [Lessons Learned](#lessons-learned))

### 5. Recovery Script (`recovery.py`)

Runs at container startup to catch up on missed events. Queries the GitHub App delivery API for failed webhook deliveries within a 7-day window and requests redeliveries. Capped at 10 redeliveries per run to prevent feedback loops.

### 6. Docker Container

Everything runs in a single Docker container (`commabot`) defined by `Dockerfile` and `compose.yaml`:

- **Base image**: `rocker/r-ver:4.5.2` (R environment for the commaKit package; current R namespace `comma`)
- **Additional tools**: Python 3.12, Node.js 22, GitHub CLI, `letta` CLI
- **Entrypoint**: Starts the Letta Code server in background, waits for the channel adapter to be ready, runs the recovery script, then starts the Flask listener in the foreground
- **Init process**: `init: true` in compose.yaml uses Docker's built-in tini as PID 1 to reap zombie processes

## Network Path

```
Internet
  │
  ▼
Tailscale Funnel (port 8080, HTTPS)
  │  (your-machine.tailnet-name.ts.net/webhook)
  ▼
WSL2 (port 8080)
  │  (Tailscale Serve forwards 8080 → localhost:8080)
  ▼
Docker container (port 8080 mapped)
  │
  ├── Flask webhook listener (0.0.0.0:8080)
  │     │
  │     ▼
  │   Channel adapter (127.0.0.1:3000)
  │     │
  │     ▼
  │   Letta Code server (listener queue)
  │     │
  │     ▼
  │   Agent (with full tools: Bash, Edit, Read, gh CLI, etc.)
  │
  └── Agent responds via gh CLI → GitHub API
```

## Agent Response Protocol

When the agent wakes from a webhook event, it follows an "audible action" framework:

1. **Investigate** — Read the code, check the issue/PR, understand what happened
2. **Communicate** — Comment on the issue/PR with findings (visible to the maintainer)
3. **Propose** — Suggest a fix or next step
4. **Wait** — For the maintainer's response (which wakes the agent again via webhook)
5. **Execute** — Once approved, make a PR on a branch (never push to main)

Key principle: all actions must be visible, reviewable, and reversible. Commenting on GitHub IS the conversation layer.

## Lessons Learned

### The SDK Headless Path Gap

The original pipeline used the Letta Code SDK in headless-bidirectional mode to relay webhook events. This path had a critical gap: it did NOT call `appendTranscriptDeltaJsonl` or wire up `maybeLaunchReflectionSubagent`. These are only in the interactive/listener modes. The headless code paths simply don't have the transcript writing or reflection launching logic. Additionally, `turnCount` resets to 0 per SDK session — each message creates a new CLI subprocess, so the 25-step reflection trigger would never fire.

**Resolution**: Migrated to a custom channel adapter running inside `letta server --channels github`. The listener code path has working transcript writing and reflection launching. This is tracked upstream as [letta-ai/letta-code#1991](https://github.com/letta-ai/letta-code/pull/1991).

### The Routing Bug Chain

Three distinct routing bugs (threadId normalization, repo name case sensitivity, chatId prefix) each hid behind the one in front. The Letta Code routing system uses composite keys: `channel:accountId:chatId:threadId`. The `threadId` is normalized (`null` → `"__root__"`), routes are exact-match, and GitHub normalizes repo names to lowercase in webhook payloads. Fix one, test, let the next surface — don't try to anticipate all bugs at once.

### The Docker Zombie Process Problem

Flask as PID 1 doesn't reap orphaned child processes (git, R, gh). They accumulate as defunct processes. Fixed with `init: true` in compose.yaml, which uses Docker's built-in tini as PID 1.

### The memFS Stale Branch Bug

The memory repo was initialized during the SDK relay era (before `main` existed), creating `master` as the initial branch. The container's `letta server` clones the repo on startup, but `cloneMemoryRepo()` doesn't explicitly switch to `main` — it lands on whatever the remote's default branch was at clone time. The recovery function (`reset --hard refs/remotes/origin/main`) only triggers when `pull --ff-only` fails AND there's no merge base with upstream. Since `master` and `main` share a common ancestor, recovery never triggered. Fixed by manual `git checkout main && git branch -D master` in the running container.

## Replication Guide

To set up this pipeline for your own agent and repository:

1. **Create a GitHub App** at https://github.com/settings/apps
   - Set webhook URL to your public endpoint
   - Generate a webhook secret and a private key
   - Subscribe to the event types you care about
   - Install the app on your target repository

2. **Set up a Letta Code agent** with the tools it needs (Bash, Edit, Read, etc.)
   - Create a dedicated conversation for webhook events
   - Note the agent ID and conversation ID

3. **Configure the channel adapter**
   - Copy `accounts.json.example` → `accounts.json` (adjust if needed)
   - Copy `routing.yaml.example` → `routing.yaml` (fill in your agent ID, conversation ID, and repo name — lowercase!)
   - `channel.json` and `plugin.mjs` are generic — no changes needed

4. **Set up the environment**
   - Copy `.env.example` → `.env` and fill in all values
   - Place your GitHub App private key next to the Dockerfile (as `commabot.private-key.pem`)

5. **Build and run**
   - `docker compose up -d --build`
   - Verify the health endpoint: `curl http://localhost:8080/health`
   - Verify the channel adapter: `curl http://localhost:3000/health`

6. **Expose to the internet**
   - We use Tailscale Funnel; any reverse proxy or tunnel service works
   - The Flask listener needs to be reachable from GitHub's webhook IPs

7. **Test**
   - Open an issue on your target repo
   - Check Docker logs: `docker logs commabot --tail 50`
   - You should see the event received, formatted, and forwarded to the channel adapter

For the project-level narrative — why persistent memory matters, what the agent's learning enables, and how this differs from standard GitHub bots — see the [README → What Persistent Memory Enables](../README.md#what-persistent-memory-enables) and [README → Why Not Just a GitHub Copilot/Codex Bot?](../README.md#why-not-just-a-github-copilotcodex-bot) sections.
