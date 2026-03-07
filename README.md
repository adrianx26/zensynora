# 🦞 MyClaw — Personal AI Agent

A powerful personal AI agent that runs locally using [Ollama](https://github.com/ollama/ollama) with llama3.2, featuring Telegram integration, persistent SQLite memory, multi-agent support, dynamic tool building, and task scheduling.

## ✨ Features

### Core Capabilities
- **Local LLM** — Runs entirely on your machine using [Ollama](https://github.com/ollama/ollama) (llama3.2 by default). No cloud dependencies, complete privacy.
- **Persistent Memory** — SQLite-backed conversation history with per-user isolation. Your chats are stored locally.
- **Tool System** — Execute shell commands, read/write files, and more—all within a secure workspace.

### Advanced Features
- **Multi-Agent Support** — Create and manage multiple named agents with custom prompts and models
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
│   │   (SQLite)   │  │  (Ollama)  │  │ shell, file, etc │  │
│   └──────────────┘  └─────────────┘  └──────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 🚀 Quick Start

### Prerequisites

- Python 3.10+
- [Ollama](https://github.com/ollama/ollama) installed and running

### Installation

```bash
# Clone the repository
git clone https://github.com/yourusername/myclaw.git
cd myclaw

# Create virtual environment
python -m venv venv

# Activate (Linux/macOS)
source venv/bin/activate

# Activate (Windows)
venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Initial Setup

```bash
# Run the onboarding wizard
python cli.py onboard
```

Edit `~/.myclaw/config.json` to configure:
- Telegram bot token (get from [@BotFather](https://tbot.botfather/))
- Your Telegram user ID (use [@userinfobot](https://t.me/userinfobot))
- Ollama endpoint (default: `http://localhost:11434`)
- Default model (default: `llama3.2`)

### Running

**Console Mode:**
```bash
python cli.py agent
```

**Telegram Gateway:**
```bash
# First, start Ollama
ollama run llama3.2

# Then start the Telegram bot
python cli.py gateway
```

---

## 📖 Usage

### Console Commands

```
Tu: Hello
Claw: [response]

Tu: @agentname message  # Route to specific agent
Tu: exit                # Quit
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
| `cancel_schedule(job_id)` | Cancel a scheduled job |
| `list_schedules()` | List active scheduled jobs |

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/remind <seconds> <message>` | Set a one-shot reminder |
| `/remind every <seconds> <message>` | Set a recurring reminder |
| `/jobs` | List all scheduled jobs |
| `/cancel <job_id>` | Cancel a job |
| `/agents` | List available agents |
| `@agentname <message>` | Route to specific agent |

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
│   ├── provider.py          # Ollama API client
│   ├── tools.py             # Tool definitions
│   └── channels/
│       ├── __init__.py
│       └── telegram.py      # Telegram bot
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
    }
  },
  "agents": {
    "defaults": {
      "model": "llama3.2"
    },
    "named": [
      {
        "name": "coder",
        "model": "llama3.2",
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

### Creating Named Agents

Add agents to the `agents.named` array in your config. Each agent can have:
- `name` — Agent identifier (use with `@name` prefix)
- `model` — Ollama model to use
- `system_prompt` — Custom system instructions

---

## 🔧 Development

### Running Tests

```bash
# Tests coming soon
```

### Adding Custom Tools

Place custom tools in `~/.myclaw/tools/` or let the agent create them dynamically using `register_tool()`.

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
