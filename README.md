# commaBot

**An AI agent that develops a Bioconductor-class scientific R package.**

commaBot autonomously maintains [commaKit](https://github.com/carl-stone/comma) — the Comparative Microbial Methylomics Analysis Kit, a Bioconductor-targeted R package for bacterial DNA methylation analysis from Nanopore sequencing. One scientist built this agent to serve as the entire engineering team for a real scientific software package.

## The Problem

Scientific software packages need dedicated engineering support — test suites, CI pipelines, schema migrations, code quality audits, documentation maintenance. Most labs don't have that. The PI writes code until it works, then moves on to the next paper. The package rots.

commaKit is a real package with real users: it handles S4 classes, Bioconductor conventions, multiple statistical backends for differential methylation, and genome-wide analysis pipelines. Maintaining it at Bioconductor quality normally requires a dedicated software engineer. Carl Stone is a computational biologist, but not a software developer — so he built one.

## What the Agent Does

commaBot operates as the full engineering team for commaKit:

- **Project management** — decomposes scientific goals into dependency-ordered issues, proposes implementation plans with migration strategies, tracks progress on GitHub
- **Development** — writes R code, S4 class definitions, roxygen2 documentation, testthat tests, and vignettes following Bioconductor conventions
- **Review** — runs `R CMD check`, audits code quality, identifies stale documentation and subtle bugs
- **CI/CD** — monitors GitHub Actions, investigates failures, proposes fixes
- **Memory management** — maintains its own conventions, design decisions, and working protocols in a git-backed memory system

What it does NOT do: merge its own PRs, push to main, or make silent fixes. Carl reviews everything. The agent proposes; Carl decides.

## Why This Is Hard

Most AI agents can write a Flask app. Writing a Bioconductor package is a different domain entirely:

**S4, not S3.** Bioconductor uses R's formal object system. Classes have validity methods, generics have dispatch rules, and the wrong accessor pattern silently corrupts data. The agent needs to understand `RangedSummarizedExperiment`, `GRanges`, `findOverlaps()`, and the difference between `mcols()` and `rowData()` — not just syntax, but the Bioconductor philosophy behind them.

**Scientific correctness, not just code correctness.** `diffMethyl()` loops by `mod_context` (e.g., `6mA:GATC`) rather than `mod_type` (e.g., `6mA`) because pooling across sequence contexts produces spurious results. Effect sizes are reported on the beta scale (0–1), not the M-value scale, because that's what biologists interpret. Multiple testing correction is genome-wide across all modification contexts. These are statistical and scientific judgment calls that get translated into reproducible code.

**Subtle wrongness is the default failure mode.** An LLM asked to write roxygen2 docs for an S4 method produces output that looks professional — correct `@param` tags, proper `@return` — but silently omits `@docType methods` and `@rdname`, uses invalid `mod_type` values like `"mC"` instead of `"5mC"`, and doesn't recognize `mcols()` as a Bioconductor accessor. The agent has to know the domain well enough to catch these.

**The full engineering lifecycle.** Schema migration with deprecation shims and test-first migration. Dependency-ordered implementation of issues and features. Code quality audits that file findings as GitHub issues before touching code. This is software engineering discipline that most human teams don't practice consistently--or have the time to learn--applied by an AI agent to a scientific package.

## How It Works

commaBot uses a webhook pipeline to receive GitHub events in real time and respond autonomously:

```
GitHub webhook → Tailscale Funnel → Flask listener → Channel adapter → Agent
```

The agent wakes when something happens on GitHub (issue opened, PR reviewed, CI check fails), investigates the situation, comments with findings, proposes a next step, and waits for Carl's response before executing. See [docs/architecture.md](docs/architecture.md) for the full technical breakdown.

## The Agent

commaBot runs on [Letta Code](https://letta.com) with persistent memory — identity, conventions, and working protocols stored in a git-backed memory filesystem that syncs across environments. The memory is what makes the agent useful beyond a single interaction: it knows the package's architecture, the conventions Carl and it agreed on, the bugs it's already seen, and the working protocols negotiated over time.

The agent operates across three execution contexts:
- **Letta Cloud** — limited tools, for discussion with Carl
- **Docker container** — full tools (R, `gh` CLI, `devtools`), for webhook-driven development work
- **Carl's laptop** — full tools, SSH access to the container

The GitHub webhook is just one input channel. The agent can run `devtools::check()` in the Docker container, SSH to the host machine to debug infrastructure issues, and discuss architecture with Carl in the desktop app — all as the same agent with the same memory.

## What the Agent Learned

The agent's process discipline wasn't designed upfront — it emerged from real failures, and each failure reshaped how the agent operates:

**Investigate before acting.** When CI failed on `\donttest{}` examples, the agent's first instinct was to push a fix immediately. Carl coached it to ask "how does this affect the package?" before acting. That lesson is now written into the agent's memory. The zoom-in/zoom-out discipline, the "start minimal" principle, the rule against fabricating explanations — these all came from specific incidents and now steer the agent automatically.

**Trusted autonomy is earned, not assumed.** The agent knows what it can do alone (label an issue, investigate a bug, run tests) and what needs Carl's approval (merge a PR, change a class contract, implement a review suggestion). It once auto-implemented a Codex review suggestion without Carl's approval; that violation became a rule. Trust is built from violations caught and corrected.

**Self-correction without micromanagement.** When the agent started receiving webhook events triggered by its own GitHub comments, it recognized the feedback loop and added a self-filter to the Flask listener — without being asked. The broader pattern: the agent catches its own behavioral failures and fixes them. Carl doesn't need to anticipate every failure mode.

**Less communication overhead over time.** The 👀 reaction protocol (acknowledge on GitHub, remove when done), the Slack-vs-GitHub boundary (conversations on Slack, durable work on GitHub), and the "start minimal" principle all came from real friction. Each protocol reduces the communication cost of the next interaction.

## Repository Structure

```
.
├── README.md                              # This file
├── docs/
│   ├── architecture.md                    # Webhook pipeline architecture
│   └── commaBenchmark.md                  # Local LLM benchmarking framework
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
│   ├── README.md                          # How to run benchmarks
│   ├── runner.py                          # Sends prompts, collects & scores responses
│   └── prompts/                           # Test case JSON files (6 categories)
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

## License

MIT
