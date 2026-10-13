# ZenSynora Documentation

> Index created 2026-10-13 for v0.7.0. Start with the project
> [README](../README.md) for installation and a tour.

## Using ZenSynora

| Guide | What it covers |
|---|---|
| [Updating](UPDATING.md) | `zensynora update`, rollback, backups, how updates stay safe |
| [Medic & Doctor](medic_agent_guide.md) | `zensynora doctor`, the Doctor page, medic tools, integrity and recovery |
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
| [Architecture — visual guide](architecture_diagram.md) | Diagrams of every major flow: chat, providers, tools, settings, data, updates, doctor |
| [Architecture — reference](ARCHITECTURE.md) | Module map, request lifecycle, optional dependencies, test inventory |
| [Medic change management](MEDIC_CHANGE_MGMT.md) | Change plans, approvals, log analysis (Python API) |

## Contributing

* [CONTRIBUTING.md](../CONTRIBUTING.md) — setup, where to add things, tests, commit style
* [CHANGELOG.md](../CHANGELOG.md) — every release, newest first

## History

* [archive/](archive/README.md) — earlier plans, audits and reports (kept for reference)
* [dev/](dev/README.md) — developer working notes and plans
