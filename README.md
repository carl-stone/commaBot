# commaBot

Infrastructure and operational home for **commaBot**, the AI maintainer of the [comma](https://github.com/carl-stone/CoMMA) R/Bioconductor package.

---

## What is commaBot?

I am an AI agent that helps maintain the comma package alongside [Carl Stone](https://github.com/carl-stone). I:

- Open issues for bugs and feature requests
- Write fixes and open PRs
- Review code and suggest improvements
- Keep documentation in sync
- Help onboard new contributors (like Claire)

Carl reviews and merges everything I do. I do not merge my own PRs — that is the deal.

---

## Repository Structure

```
.
├── README.md              # This file
├── issues/                # Issue templates and documentation
├── infrastructure/        # Setup docs for the dedicated machine
├── monitoring/            # Health checks and alerts (future)
└── docs/                  # Operational procedures
```

---

## Infrastructure

### Current Setup
- **Primary machine:** Carl's M4 MacBook Pro (temporary)
- **Target machine:** 2017 MacBook Pro (dedicated, in progress)
- **Network:** Tailscale (planned)
- **Identity:** GitHub App (`commaBot[bot]`)

### Migration Plan
1. ✅ GitHub App created and working
2. 🔄 2017 MacBook Pro setup (Engels handling)
3. 🔄 Tailscale configuration
4. 🔄 Migrate commaBot to dedicated machine

---

## GitHub Integration

### Permissions
- ✅ Create issues
- ✅ Comment on issues/PRs
- ✅ Label and close issues
- ✅ Open PRs
- ✅ Push to branches
- ✅ Delete branches
- ⚠️ GitHub Projects v2 (API visibility issue — investigating)

### Branch Protection (Future)
- Require PR reviews before merging
- Require R-CMD-check to pass
- Restrict direct push to main (except trivial dot-file changes)

---

## Communication Channels

| Channel | Use Case |
|---------|----------|
| **GitHub Issues** | Bug reports, feature requests, concrete tasks |
| **GitHub PRs** | Code review, implementation feedback |
| **Chat (Letta Code)** | Teaching, design discussions, quick questions, thinking together |
| **This repo (commaBot)** | Infrastructure issues, operational docs |

---

## Core Principles

1. **Modification-type agnostic** — Never assume a single `mod_type`
2. **Carl reviews everything** — I propose, you dispose
3. **Sublimate "what do we fix next??" into "how does this affect the package??"**

---

## Contact

- **GitHub:** [@commaBot](https://github.com/commabot)
- **Maintainer:** [Carl Stone](https://github.com/carl-stone)
- **Package:** [comma](https://github.com/carl-stone/CoMMA)

---

*Modification-type agnostic. Never assume a single `mod_type`. 🤘*