# 🦞 MyClaw — Personal AI Agent

A powerful personal AI agent that runs locally or in the cloud using various LLM providers, featuring Telegram integration, persistent SQLite memory, multi-agent support, dynamic tool building, and task scheduling.

## ✨ Features

### Core Capabilities
- **Flexible LLM Providers** — Run locally using [Ollama](https://github.com/ollama/ollama), LM Studio, or llama.cpp, or connect to cloud providers like OpenAI, Anthropic, Gemini, Groq, and OpenRouter. Complete flexibility to choose your model and privacy level.
- **Persistent Memory** — SQLite-backed conversation history with per-user isolation. Your chats are stored securely.
- **Tool System** — Execute shell commands, read/write files, and more—all within a secure workspace.

### Advanced Features
- **Multi-Agent Support** — Create and manage multiple named agents with custom prompts and models
- **Per-Agent Prompt Profiles** — Manage individual agent system prompts using dedicated Markdown files (`~/.myclaw/profiles/{name}.md`)
- **Agent Delegation** — Delegate tasks to specialized agents (e.g., `@coder write a function`)
- **Dynamic Tool Building** — The agent can create and register new Python tools at runtime
- **Task Scheduling** — Schedule one-shot or recurring tasks with Telegram notifications
- **Telegram Gateway** — Full-featured Telegram bot with commands: `/remind`, `/jobs`, `/cancel`, `/agents`

### Security
- Command allowlist/blocklist for shell execution
- Path validation to prevent directory traversal attacks
- Per-user memory isolation
- Configurable Telegram access control (whitelist by user ID)

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Channels (CLI, Telegram)                  │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│                         Agent                                │
│   ┌──────────────┐  ┌─────────────┐  ┌──────────────────┐  │
│   │    Memory    │  │  Provider  │  │      Tools       │  │
│   │   (SQLite)   │  │ (Ollama,   │  │ shell, file, etc │  │
│   │              │  │  OpenAI,..)│  │                  │  │
│   └──────────────┘  └─────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- [Optional] [Ollama](https://github.com/ollama/ollama), LM Studio, or API keys for Cloud Providers

### Installation

#### 🐧 Linux / Ubuntu (recommended)

Use the automated install script — it checks and installs every system package, Python dependency, and optional LLM SDK:

```bash
git clone https://github.com/adrianx26/zensynora.git
cd zensynora
chmod +x install.sh
./install.sh
```

The script handles:
- ✅ System packages (`python3`, `pip`, `venv`, `git`, `curl`, `sqlite3`, etc.)
- ✅ Python ≥ 3.10 (auto-upgrades via deadsnakes PPA if needed)
- ✅ Virtual environment creation & activation
- ✅ All pip dependencies from `requirements.txt`
- ✅ Optional LLM SDKs (Anthropic, Gemini — prompted interactively)
- ✅ Optional [Ollama](https://github.com/ollama/ollama) install for local models
- ✅ Required data directories (`~/.myclaw/`)
- ✅ Optional systemd service to auto-start the Telegram gateway on boot
- ✅ Final import verification of all installed packages

#### 🪟 Windows / Manual

```bash
# Clone the repository
git clone https://github.com/adrianx26/zensynora.git
cd zensynora

# Create & activate virtual environment
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # macOS / Linux (manual)

# Install dependencies
pip install -r requirements.txt
```

### Initial Setup

```bash
# Run the onboarding wizard
python cli.py onboard
```

Edit `~/.myclaw/config.json` to configure:
- Telegram bot token (get from [@BotFather](https://t.me/botfather))
- Your Telegram user ID (use [@userinfobot](https://t.me/userinfobot))
- **Providers:** Configure APIs for Ollama, OpenAI, Anthropic, Gemini, Groq, OpenRouter, LM Studio, or llama.cpp.

### Running

**Console Mode:**
```bash
python cli.py agent
```

**Telegram Gateway:**
```bash
# First, ensure your chosen provider is running or configured
ollama run llama3.2

# Then start the Telegram bot
python cli.py gateway
```

---

## 📖 Usage

### Console Commands

```
You: Hello
Claw: [response]

You: @agentname message  # Route to specific agent
You: exit                # Quit
```

### Available Tools

| Tool | Description |
|------|-------------|
| `shell(cmd)` | Execute allowed shell commands |
| `read_file(path)` | Read a file from workspace |
| `write_file(path, content)` | Write a file to workspace |
| `delegate(agent_name, task)` | Delegate to another agent |
| `list_tools()` | List all available tools |
| `register_tool(name, code)` | Create a new Python tool |
| `schedule(task, delay, every)` | Schedule a task |
| `edit_schedule(job_id, ...)` | Edit active schedule task/delay |
| `split_schedule(job_id, tasks)` | Split job into sub-tasks (JSON array) |
| `suspend_schedule(job_id)` | Pause an active scheduled job |
| `resume_schedule(job_id)` | Resume a suspended job |
| `cancel_schedule(job_id)` | Cancel a scheduled job |
| `list_schedules()` | List active scheduled jobs |
| `write_to_knowledge(title, content)` | Save note to knowledge base |
| `search_knowledge(query)` | Search knowledge with FTS5 |
| `read_knowledge(permalink)` | Read a knowledge note |
| `get_knowledge_context(permalink, depth)` | Get related knowledge |
| `list_knowledge()` | List all knowledge notes |
| `sync_knowledge_base()` | Sync knowledge with files |
| `list_knowledge_tags()` | List all knowledge tags |

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/remind <seconds> <message>` | Set a one-shot reminder |
| `/remind every <seconds> <message>` | Set a recurring reminder |
| `/jobs` | List all scheduled jobs |
| `/cancel <job_id>` | Cancel a job |
| `/agents` | List available agents |
| `@agentname <message>` | Route to specific agent |
| `/knowledge_search <query>` | Search knowledge base |
| `/knowledge_list` | List all knowledge notes |
| `/knowledge_read <permalink>` | Read a specific note |
| `/knowledge_write <title> | <content>` | Create a new note |
| `/knowledge_sync` | Sync knowledge with files |
| `/knowledge_tags` | List all tags |

---

## 📚 Knowledge Base

MyClaw includes a powerful **knowledge storage system** inspired by [MemoPad](https://github.com/adrianx26/memopad), using Markdown files with SQLite indexing.

### Features

- **Markdown-first**: All notes are stored as plain Markdown files
- **Full-text search**: SQLite FTS5 for fast searching
- **Knowledge graph**: Relations between entities
- **Observations**: Structured facts with categories and tags
- **Multi-user**: Per-user isolation with separate directories

### Storage Location

Knowledge files are stored in:
```
~/.myclaw/knowledge/{user_id}/
```

Each user has their own:
- Directory: `~/.myclaw/knowledge/{user_id}/`
- Database: `~/.myclaw/knowledge_{user_id}.db`

### Markdown Format

```markdown
---
title: "Project Phoenix"
permalink: project-phoenix
tags: [work, urgent]
created: 2026-03-08T10:00:00
updated: 2026-03-08T15:30:00
---

# Project Phoenix

## Observations
- [status] Active development phase #work
- [milestone] Backend API completed on March 5th
- [risk] Database migration needs testing

## Relations
- leads [[team-backend]]
- depends_on [[infrastructure-v2]]
- blocks [[mobile-app-v3]]
```

### CLI Commands

```bash
# Search knowledge
python cli.py knowledge search "project phoenix"

# Create a new note (interactive)
python cli.py knowledge write

# Read a specific note
python cli.py knowledge read project-phoenix

# List all notes
python cli.py knowledge list

# Sync database with files
python cli.py knowledge sync

# List all tags
python cli.py knowledge tags
```

### Using in Conversations

The agent automatically searches the knowledge base when processing messages. You can also reference knowledge explicitly:

```
You: Tell me about memory://project-phoenix
Claw: [Searches knowledge and responds with relevant info]

You: Save this: Project Phoenix is now in testing phase
Claw: [Uses write_to_knowledge tool to save the note]
```

---

## 📁 Project Structure

```
myclaw/
├── myclaw/
│   ├── __init__.py          # Package init
│   ├── agent.py             # Core agent logic
│   ├── config.py            # Configuration management
│   ├── gateway.py           # Channel routing
│   ├── memory.py            # SQLite persistence
│   ├── provider.py          # LLM Provider abstraction
│   ├── tools.py             # Tool definitions
│   ├── channels/
│   │   ├── __init__.py
│   │   └── telegram.py      # Telegram bot
│   └── knowledge/           # Knowledge storage system
│       ├── __init__.py
│       ├── db.py            # SQLite database
│       ├── parser.py        # Markdown parsing
│       ├── storage.py       # File operations
│       ├── graph.py         # Graph traversal
│       └── sync.py          # File-DB sync
├── onboard.py               # Setup wizard
├── cli.py                   # CLI entry point
├── requirements.txt         # Dependencies
└── README.md                # This file
```

---

## ⚙️ Configuration

Configuration is stored in `~/.myclaw/config.json`:

```json
{
  "providers": {
    "ollama": {
      "base_url": "http://localhost:11434"
    },
    "openai": {
      "api_key": "YOUR_OPENAI_KEY"
    },
    "anthropic": {
      "api_key": "YOUR_ANTHROPIC_KEY"
    }
  },
  "agents": {
    "defaults": {
      "provider": "ollama",
      "model": "llama3.2"
    },
    "named": [
      {
        "name": "coder",
        "provider": "openai",
        "model": "gpt-4o",
        "system_prompt": "You are a coding assistant..."
      }
    ]
  },
  "channels": {
    "telegram": {
      "enabled": true,
      "token": "YOUR_BOT_TOKEN",
      "allowFrom": ["YOUR_USER_ID"]
    }
  }
}
```

### Supported Providers
- **Local**: `ollama`, `lmstudio`, `llamacpp`
- **Cloud**: `openai`, `anthropic`, `gemini`, `groq`, `openrouter`

### LM Studio Configuration

For LM Studio integration (running on a remote server):

```json
{
  "providers": {
    "lmstudio": {
      "base_url": "http://192.168.8.112:1234/v1",
      "api_key": "test123"
    }
  },
  "agents": {
    "defaults": {
      "model": "llama-3.2-3b-instruct-uncensored@q4_k_s",
      "provider": "lmstudio"
    }
  }
}
```

**Note**: LM Studio API token can be any non-empty string for testing purposes.

### Creating Named Agents

Add agents to the `agents.named` array in your config. Each agent can have:
- `name` — Agent identifier (use with `@name` prefix)
- `provider` — The LLM provider to use (e.g., `openai`, `ollama`)
- `model` — Provider-specific model name
- `system_prompt` — Custom system instructions

**Per-Agent Prompt Profiles:**
Alternatively, an agent's individual system prompt can be managed via dedicated Markdown files instead of the config. MyClaw will automatically load the prompt from `~/.myclaw/profiles/{name}.md` upon startup if the file exists. This allows for rich, multi-line instructions easily.

---

## 🔧 Development

### Running Tests

```bash
# Activate virtual environment
source venv/bin/activate

# Run all tests
python -m pytest tests/ -v

# Run specific test files
python -m pytest tests/test_agent.py -v
python -m pytest tests/test_knowledge.py -v
python -m pytest tests/test_tools.py -v
python -m pytest tests/test_memory.py -v

# Run tests with coverage
pip install coverage
coverage run -m pytest tests/ -v
coverage report -m
```

### Adding Custom Tools

Place custom tools in `~/.myclaw/tools/` or let the agent create them dynamically using `register_tool()`.

### Cleanup

A `cleanup.sh` script is provided to remove temporary files:

```bash
chmod +x cleanup.sh
./cleanup.sh
```

This will:
- Remove test files (`test_*.py`)
- Remove temporary files (`*.tmp`)
- Remove downloaded archives (`*.zip`)
- Remove Putty tools directory (`putty/`)
- Remove pytest cache (`__pycache__/`, `.pytest_cache/`)

---

## ⚠️ Security Notes

- The agent executes shell commands—review the allowlist in [`myclaw/tools.py`](myclaw/tools.py:19)
- File operations are restricted to the workspace directory (`~/.myclaw/workspace`)
- Telegram access is controlled by user ID whitelist
- Always review what the agent executes, especially with shell commands

---

## 🤝 Contributing

Contributions welcome! Please feel free to submit issues and pull requests.

---

## 📜 License

MIT License — see LICENSE file for details.

---

## 🔗 Links

- [Ollama](https://github.com/ollama/ollama)
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [Pydantic](https://docs.pydantic.dev/)
