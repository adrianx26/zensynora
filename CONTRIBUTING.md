# Contributing to ZenSynora

Thank you for your interest in contributing to ZenSynora (MyClaw)! This document provides guidelines and instructions to help you get started.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Coding Standards](#coding-standards)
- [How to Add a New Tool / Skill](#how-to-add-a-new-tool--skill)
- [Testing](#testing)
- [Submitting Changes](#submitting-changes)
- [Commit Message Guidelines](#commit-message-guidelines)
- [Getting Help](#getting-help)

---

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/zensynora.git
   cd zensynora
   ```
3. **Create a branch** for your changes:
   ```bash
   git checkout -b feature/your-feature-name
   # or
   git checkout -b fix/issue-description
   ```

## Development Setup

### Prerequisites
- Python 3.11 or higher
- Git
- (Optional) Ollama or another LLM provider for local testing

### Install in Development Mode
```bash
# Create a virtual environment
# If `venv` is unavailable on Debian/Ubuntu: sudo apt install python3-venv
python3 -m venv .venv
source .venv/bin/activate  # Linux / macOS

# Install in editable mode
python -m pip install -e .

# Verify installation
zensynora --help
```

On Windows PowerShell, use `py -m venv .venv`, activate with
`.\.venv\Scripts\Activate.ps1`, then run `python -m pip install -e .`.
Always install into this virtual environment; do not use system pip or
`--break-system-packages` on Debian/Ubuntu.

The `install.sh` setup routine checks Python's venv support and offers to
install `python3-venv` on Debian/Ubuntu when needed. It also validates an
existing `venv/` and offers to recreate it if it is broken; pass
`--no-docker --recreate-venv` to request a clean environment directly.

### Environment Configuration
```bash
# Copy the example environment file
cp .env.example .env

# Edit .env with your API keys and configuration
# Required for basic operation:
#   - At least one LLM provider (OLLAMA_URL, OPENAI_API_KEY, etc.)
```

## Project Structure

```
zensynora/
├── myclaw/                 # Core Python package
│   ├── agent.py            # Main agent logic
│   ├── config.py           # Configuration management
│   ├── gateway.py          # `zensynora gateway`: Telegram/WhatsApp bots without the Web UI
│   ├── memory.py           # SQLite memory system
│   ├── provider.py         # LLM provider abstraction
│   ├── tools/               # Tool system (shell, files, web, kb, scheduler, swarm, toolbox)
│   ├── channels/           # Messaging channels (telegram.py: the Telegram bot)
│   ├── knowledge/          # Knowledge base (DB, graph, parser)
│   ├── profiles/           # Agent personality profiles
│   └── swarm/              # Multi-agent swarm orchestration
├── webui/                  # FastAPI + React Web UI
├── docs/                   # User & developer documentation
│   └── dev/                # Development plans & analysis
├── eval/                   # Evaluation & benchmarking scripts
├── tests/                  # Unit & integration tests
├── cli.py                  # Command-line entry point
├── onboard.py              # Setup wizard
└── requirements.txt        # Python dependencies
```

## Coding Standards

### Python Style
- Follow **PEP 8** guidelines
- Use **type hints** for function signatures where practical
- Keep functions focused and under ~50 lines when possible
- Document public functions with docstrings:
  ```python
  def process_message(user_id: str, text: str) -> str:
      """Process an incoming user message and return a response.
      
      Args:
          user_id: Unique identifier for the user.
          text: The raw message text.
          
      Returns:
          The agent's response string.
      """
  ```

### Code Quality Tools

We use `pre-commit` hooks to enforce code quality automatically. Install once:

```bash
python -m pip install -e ".[dev]"  # Run inside the activated .venv
pre-commit install           # Activates git hooks
pre-commit run --all-files   # Run all checks manually (optional)
```

The following checks run on every commit and in CI:

| Tool | Purpose | Command |
|------|---------|---------|
| **ruff** | Fast Python linter (replaces flake8, pydocstyle, pyupgrade) | `ruff check myclaw/ cli.py onboard.py deploy.py` |
| **ruff-format** | Fast Python formatter | `ruff format myclaw/ cli.py onboard.py deploy.py` |
| **black** | Uncompromising code formatter | `black --check myclaw/ cli.py onboard.py deploy.py` |
| **isort** | Import sorting | `isort --check-only myclaw/ cli.py onboard.py deploy.py` |
| **pytest** | Unit & integration tests | `pytest tests/ -v --tb=short` |

To auto-fix most lint/format issues:
```bash
ruff check . --fix
ruff format .
black .
isort .
```

### Key Principles
- **Security first**: Validate all paths, sanitize inputs, never expose secrets
- **Modularity**: Keep tools and channels loosely coupled
- **Backward compatibility**: Avoid breaking changes to the config schema
- **Error handling**: Use the project's custom exception hierarchy in `myclaw/exceptions.py`

## Where to Add Things

| You are adding… | Put it in | Docs |
|---|---|---|
| A tool the agents can call | `myclaw/tools/` + register in `myclaw/tools/__init__.py` | below |
| A health check | one `@check` function in `myclaw/doctor/checks.py` | [docs/medic_agent_guide.md](docs/medic_agent_guide.md#adding-a-check) |
| A change to stored data (config keys, DB schema, folders) | a migration `updater/migrations/m_NNNN_name.py` | [docs/UPDATING.md](docs/UPDATING.md#add-a-data-migration) |
| A new update step | a `Step` in `updater/steps.py` | [docs/UPDATING.md](docs/UPDATING.md#add-or-change-an-update-step) |
| A Web UI page | `webui/src/components/` + `NavButton` in `App.tsx` + a router in `myclaw/web/` included in `api.py` | [webui/README.md](webui/README.md) |
| Something agents do that the chat should show | `myclaw.events.emit({"type": …})` (+ a card in `ChatView.tsx` `eventSummary`) | [docs/architecture_diagram.md §12](docs/architecture_diagram.md#12-chat-channels-and-agent-to-agent-talk) |
| A provider preset (local or online) | `PROVIDER_PRESETS` in `myclaw/web/settings_api.py` | [docs/webui_guide.md](docs/webui_guide.md#models-switch-rotate-add-providers) |
| A job template | `TEMPLATES` in `webui/src/components/JobsView.tsx` | [docs/webui_guide.md](docs/webui_guide.md#jobs-scheduled-and-on-demand-tasks) |
| Data that should be in backups | `_PART_PATTERNS` in `myclaw/backup.py` | [docs/webui_guide.md](docs/webui_guide.md#backups-settings-api-keys-knowledge) |
| A Telegram command | a handler in `myclaw/channels/telegram.py` (it is added to the bot's `/` menu with its description); Web UI status in `myclaw/web/telegram_api.py` | [docs/telegram_guide.md](docs/telegram_guide.md) |
| An LLM provider | `myclaw/provider.py` (+ SDK in `requirements.txt` if it is needed at chat time) | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |

Every user-visible change gets a line in `CHANGELOG.md` under `[Unreleased]`.

## How to Add a New Tool / Skill

Tools are the primary way to extend ZenSynora's capabilities.

### 1. Define the Tool Function
Create a new function in `myclaw/tools/` (add to appropriate module or create new one):

```python
from myclaw.exceptions import ToolExecutionError

def my_new_tool(param1: str, param2: int = 10) -> dict:
    """Brief description of what this tool does.
    
    Args:
        param1: Description of param1.
        param2: Description of param2 (default: 10).
        
    Returns:
        A dictionary with the result.
        
    Raises:
        ToolError: If the operation fails.
    """
    try:
        result = do_something(param1, param2)
        return {"success": True, "data": result}
    except Exception as e:
        raise ToolExecutionError(f"my_new_tool failed: {e}")
```

### 2. Register the Tool
Add the tool to the allowlist and description registry so the LLM can use it:

```python
# In myclaw/agent.py or the relevant module
from myclaw.tools import my_new_tool

# Add to the tool registry
TOOL_REGISTRY = {
    # ... existing tools ...
    "my_new_tool": my_new_tool,
}

# Add to the tool descriptions (used in system prompt)
TOOL_DESCRIPTIONS = {
    # ... existing descriptions ...
    "my_new_tool": "my_new_tool(param1: str, param2: int = 10) -> dict: Brief description.",
}
```

### 3. Add Tests
Add tests to `tests/test_tools.py` or a new `tests/test_<feature>.py`:

```python
def test_my_new_tool():
    result = my_new_tool("test", param2=5)
    assert result["success"] is True
    assert "data" in result
```

### 4. Document
- Update the README if the tool is user-facing
- Add an entry to the tool catalog if one exists

## Testing

### Running Tests
```bash
# Run all tests (addopts="" skips the 60 % coverage gate from pyproject.toml)
pytest -q -o addopts=""

# Run with coverage
pytest --cov=myclaw --cov-report=html

# Run specific test file
pytest tests/test_tools.py

# Run with verbose output
pytest -v
```

### Test Categories
- **Unit tests** (`tests/test_*.py`): Test individual functions in isolation
- **Integration tests**: Test gateway ↔ agent ↔ provider flow
- **Security tests** (`tests/test_security.py`): Path traversal, injection, allowlist
- **Telegram tests** (`tests/test_telegram_channel.py`, `tests/test_telegram_webui.py`): the Telegram bot (including an end-to-end run of python-telegram-bot against the local fake Bot API server in `tests/fixtures/fake_telegram_api.py`, selected with `ZENSYNORA_TELEGRAM_API_URL`), and the bot inside the Web UI

### Mocking External Services
When testing LLM-dependent code, mock the provider responses:

```python
from unittest.mock import patch, MagicMock

def test_agent_with_mock_provider():
    mock_provider = MagicMock()
    mock_provider.chat.return_value = {"content": "Test response"}
    
    agent = Agent(provider=mock_provider)
    response = agent.process("Hello")
    
    assert response == "Test response"
```

## Submitting Changes

1. **Ensure tests pass**:
   ```bash
   pytest
   ```

2. **Update documentation** if your change affects user-facing behavior

3. **Fill out the Pull Request template** completely — it helps reviewers understand your changes

4. **Link related issues** using `Fixes #123` or `Relates to #456`

5. **Be responsive** to review feedback — we aim to merge PRs within a few days

## Commit Message Guidelines

We follow conventional commit-style messages for clarity:

```
<type>(<scope>): <description>

[optional body]

[optional footer]
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `style`: Formatting, missing semicolons, etc. (no code change)
- `refactor`: Code refactoring
- `perf`: Performance improvement
- `test`: Adding or updating tests
- `chore`: Build process, dependencies, etc.

**Examples:**
```
feat(tools): add web search tool using DuckDuckGo

fix(gateway): handle Telegram webhook timeout gracefully

docs(readme): update Docker setup instructions

test(swarm): add voting strategy integration tests
```

## Getting Help

- **Questions?** Open a [Discussion](https://github.com/adrianx26/zensynora/discussions) on GitHub
- **Found a bug?** [Open an issue](https://github.com/adrianx26/zensynora/issues/new?template=bug_report.md)
- **Want a feature?** [Request it](https://github.com/adrianx26/zensynora/issues/new?template=feature_request.md)
- **Security issue?** Please email the maintainer directly instead of opening a public issue

---

## License

By contributing to ZenSynora, you agree that your contributions will be licensed under the [AGPL-3.0 License](LICENSE).

**Thank you for helping make ZenSynora better!**
