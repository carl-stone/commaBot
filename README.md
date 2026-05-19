# commaBot

Infrastructure and operational home for **commaBot**, an AI agent that autonomously maintains the [comma](https://github.com/carl-stone/CoMMA) R/Bioconductor package.

## What is commaBot?

commaBot is a persistent LLM-powered agent with its own GitHub identity (`commabot[bot]`), its own memory, and its own working protocols. It operates alongside [Carl Stone](https://github.com/carl-stone) — Carl is the domain expert and product owner; commaBot is the engineering team.

What commaBot does:
- Monitors GitHub activity on the comma repo in real time (via webhooks)
- Investigates issues, proposes fixes, and opens PRs
- Runs tests, checks CI, and keeps documentation in sync
- Manages its own memory and learns from experience

What commaBot does NOT do:
- Merge its own PRs (Carl reviews and merges everything)
- Push directly to main (all nontrivial changes go through PRs)
- Make silent fixes (all actions are visible, reviewable, and reversible)

## How It Works

commaBot uses a webhook pipeline to receive GitHub events in real time and respond autonomously:

```
GitHub webhook → Tailscale Funnel → Flask listener → Channel adapter → Agent
```

The agent wakes when something happens on GitHub (issue opened, PR reviewed, CI check fails), investigates the situation, comments with findings, proposes a next step, and waits for Carl's response before executing. See [docs/architecture.md](docs/architecture.md) for the full technical breakdown.

## Repository Structure

```
.
├── README.md                              # This file
├── docs/
│   └── architecture.md                    # Webhook pipeline architecture
├── infrastructure/
│   ├── webhook-listener.py                # Flask webhook receiver
│   ├── recovery.py                        # Startup recovery (redelivers missed events)
│   ├── commabot-github-token.py           # GitHub App token utility
│   ├── entrypoint.sh                      # Docker entrypoint
│   ├── Dockerfile                         # Container definition
│   ├── compose.yaml                       # Docker Compose (template)
│   ├── .env.example                       # Template for secrets (NEVER commit .env)
│   ├── commabot-docker.service            # systemd unit file
│   └── channels/
│       └── github/
│           ├── channel.json               # Channel metadata
│           ├── plugin.mjs                 # Letta Code channel adapter
│           ├── accounts.json.example      # Template for account config
│           └── routing.yaml.example       # Template for route config
└── .gitignore
```

## Key Design Decisions

**One-way inbound channel.** The agent receives events through the channel adapter but does NOT send replies through it. All GitHub interactions happen via the `gh` CLI (Bash tool). The `MessageChannel` tool is used only for status receipts. This keeps the agent's GitHub actions auditable and avoids the framework's `sendMessage()` dead code path.

**Infrastructure parses, agent decides.** The Flask listener handles structured data extraction (event type, sender, repo, comment IDs). The agent handles judgment and action. Push parsing to the infrastructure layer, not the agent layer.

**Self-filtering.** The Flask listener skips events where the sender is `commabot[bot]` to prevent feedback loops — the agent's own GitHub actions would trigger it again. The agent implemented this itself: Carl and the agent had discussed the principle, but when the agent started receiving noisy notifications from its own activity, it recognized the problem, added the filter to the Flask listener, and made the change without being asked.

**Fire-and-forget.** The Flask listener returns 200 to GitHub immediately after the channel adapter accepts the message. The agent processes the event asynchronously via the listener queue.

**Separation of secrets.** All credentials (webhook secret, API keys, private keys) live in `.env` (gitignored). The repo contains only `.env.example` templates and source code.

## Setup

See [docs/architecture.md → Replication Guide](docs/architecture.md#replication-guide) for step-by-step instructions to set up this pipeline for your own agent and repository.

Quick start:

1. Create a GitHub App with webhook permissions
2. Copy `.env.example` → `.env` and fill in your credentials
3. Copy `accounts.json.example` → `accounts.json` and `routing.yaml.example` → `routing.yaml`
4. Place your GitHub App private key as `commabot.private-key.pem`
5. `docker compose up -d --build`

## The Agent

commaBot runs on [Letta Code](https://letta.com) and has:

- **Persistent memory** — identity, conventions, and working protocols stored in a git-backed memory filesystem that syncs across environments
- **Multiple execution contexts** — Letta Cloud (limited tools, for discussion), Docker container (full tools, for webhook-driven work), and Carl's laptop (full tools, SSH access to the container)
- **Process discipline** — investigates before acting, proposes before executing, documents findings before fixing
- **Zoom-in/zoom-out thinking** — considers the whole repo before starting work, then focuses on the specific change, then checks for side effects

The agent's memory and persona are managed separately from this infrastructure repo. The memory lives in Letta Cloud; this repo is the operational layer that makes the agent accessible to GitHub.

## License

MIT
