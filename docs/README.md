# ZenSynora Documentation

> Current release: **v0.8.5** (2026-11-27). Start with the project
> [README](../README.md) for installation and a tour.

## Using ZenSynora

| Guide | What it covers |
|---|---|
| [Web UI guide](webui_guide.md) | Chat channels, Models, Tools and Skills, Jobs and opt-in insights, detailed Backups/Restore/Updates, Doctor investigations and GitHub reinstall, the Telegram bot |
| [Rules](rules_guide.md) | Rules every agent must follow; agents propose changes, you approve or reject them |
| [Customize](customize_guide.md) | How agents communicate, structure answers, behave and which tools they use — profiles per situation and agent, AI review, Telegram teams (0.8.1) |
| [Knowledge base](knowledge_base_guide.md) | Note format (tags, aliases, metadata, facts, links), search syntax, what agents see, CLI, `.trash`, `.bmignore` |
| [Telegram bot](telegram_guide.md) | Set up the Telegram bot, allow users, commands, `@agent` messages, files and photos, reminders, knowledge, troubleshooting |
| [Updating](UPDATING.md) | Modular update steps, colored outcomes, optional Continue/Skip/Abort decisions, mandatory data backups, rollback and approved GitHub reinstall |
| [Medic & Doctor](medic_agent_guide.md) | Health checks, reviewed investigation reports for GitHub, code reinstall, integrity/recovery, self-update proposals and learning |
| [Choosing an LLM](llm_recommendations.md) | Which local model for which hardware; llama.cpp / Ollama / LM Studio setup |
| [Web UI](../webui/README.md) | Pages, what each one talks to, developing the front end |
| [Agent catalog](agent_catalog.md) | The 137 built-in agent profiles |
| [Agent swarms](agent_swarm_guide.md) | Parallel, sequential, hierarchical and voting swarms |
| [Scheduler features](scheduler_features_guide.md) | Reliable scheduled jobs: validation, retries, checkpoints |
| [Task timer](TASK_TIMER_INTEGRATION.md) | Progress and time tracking for long tasks |
| [Execution backends](backends_guide.md) | Run commands locally, in Docker, over SSH or in WSL2 |
| [Web scraping (Scrapling)](scrapling_agent_guide.md) | Anti-bot scraping for agents |
| [External skills](skill_adapter_guide.md) | Importing skills from other ecosystems |
| [NewTech agent](newtech_agent_guide.md) | AI-news monitoring and roadmap proposals |

## How it works

| Document | What it covers |
|---|---|
| [Architecture — visual guide](architecture_diagram.md) | Diagrams of every major flow: chat, channels, providers and rotation, tools, settings, data, updates, doctor, jobs, self-update |
| [Architecture — reference](ARCHITECTURE.md) | Module map, request lifecycle, optional dependencies, test inventory |
| [Medic change management](MEDIC_CHANGE_MGMT.md) | Change plans, approvals, log analysis (Python API) |
| [Incidents and lessons](incidents_and_lessons.md) | Real problems on a running install: cause, fix, how it is caught now, and the rules that follow |

## Contributing

* [CONTRIBUTING.md](../CONTRIBUTING.md) — setup, dependency updates, generated diagrams, tests and commit style
* [CHANGELOG.md](../CHANGELOG.md) — every release, newest first

Graphify output is optional generated developer tooling and is not part of the
application, install, CI, update, or deployment workflows.

Maintenance diagrams and release flows are in the
[visual architecture guide](architecture_diagram.md). Historical plans,
feature introduction versions and agent/profile Markdown are preserved
rather than relabeled as new release content.

## History

* [archive/](archive/README.md) — earlier plans, audits and reports (kept for reference)
* [dev/](dev/README.md) — developer working notes and plans
