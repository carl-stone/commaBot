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
├── benchmark/
│   ├── README.md                        # How to run benchmarks
│   ├── runner.py                        # Sends prompts, collects & scores responses
│   └── prompts/                         # Test case JSON files (6 categories)
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

## Why Not Just a GitHub Copilot/Codex Bot?

Standard GitHub integrations (Copilot, Codex, etc.) can respond to events on GitHub too. Here's what's different:

**Persistent memory and identity.** A standard GitHub bot is stateless — each event triggers a fresh context with no memory of what happened before. commaBot wakes up with its full memory: the conventions Carl and it agreed on, the architecture decisions, the bugs it's already seen, the working protocols negotiated over time. A stateless bot would need to be told all of that every time, or it would violate those constraints immediately.

**Operates across environments, not just on GitHub.** Standard bots live entirely inside GitHub — they comment on PRs and that's it. commaBot has three execution contexts: Letta Cloud (for discussion with Carl), the Docker container (for webhook-driven work with full tools), and Carl's laptop (for SSH access to the container). It can run `devtools::check()` in the Docker container, SSH to the host machine to debug routing issues, and discuss architecture with Carl in the desktop app — all as the same agent with the same memory. The GitHub webhook is just one input channel.

**Process discipline, not just code generation.** Standard bots are reactive: you @-mention them, they generate code. commaBot has a stewardship stance — it investigates before acting, proposes before executing, documents findings before fixing. When CI failed on `\donttest{}` examples, it didn't just push a fix — it first asked how the failure affected the package, documented the finding, and then proposed the fix. Sometimes the right move is to say "not yet."

**The human-agent relationship is negotiated, not assumed.** Carl and commaBot developed working protocols over time through actual collaboration. The zoom-in/zoom-out discipline, the "start minimal" principle, the 👀 reaction pattern — these emerged from real friction and real learning. A standard bot assumes a single interaction model (you ask, it answers). This is a relationship that evolved.

**The agent modifies its own behavior.** The self-filtering story is the clearest example, but it's not the only one. When commaBot jumped to fix `\donttest{}` examples instead of asking how the failure affected the package, Carl coached it, and it wrote that lesson into its memory so its future self would think differently. A standard bot can't do this — it has no mechanism to rewrite its own instructions based on experience.

**The honest caveat:** Standard bots are faster, cheaper, and more reliable for simple tasks. If you just want a bot that auto-reviews PRs for style issues, Copilot does that fine. commaBot is a different kind of thing — a long-term engineering partner with memory and judgment, not a code generator. The tradeoff is complexity and cost.

## License

MIT
