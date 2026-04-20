# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---
## Development Workflow

### Prerequisites
- Python 3.10+
- System dependencies (e.g., `git`, `curl`, `sqlite3`)

### Installation
```bash
# Clone the repository
git clone https://github.com/adrianx26/zensynora.git
cd zensynora
chmod +x install.sh
./install.sh
# or manually:
python -m venv venv
source venv/bin/activate   # Linux / macOS
# venv\Scripts\activate    # Windows
pip install -r requirements.txt
```

### Quick Start
1. Run the onboarding wizard to generate `~/.myclaw/config.json`:
   ```bash
   python cli.py onboard
   ```
2. Choose your LLM provider, API keys, and optional Telegram/WhatsApp credentials.
3. Start an agent:
   ```bash
   python cli.py agent
   ```

### Running Tests
```bash
# Activate virtual environment
source venv/bin/activate

# Run all tests
python -m pytest tests/ -v

# Run specific test files
python -m pytest tests/test_agent.py -v
python -m pytest tests/test_provider_retry.py -v
python -m pytest tests/test_swarm_aggregation.py -v
python -m pytest tests/test_memory_batching.py -v
python -m pytest tests/test_tool_rate_limiting.py -v

# Run with coverage
python -m pytest tests/ -v --cov=myclaw --cov-report=html
```

### Linting & Formatting
```bash
# Install linting tools
pip install flake8 black isort
# Lint the codebase
flake8 .
# Auto‑format with black
black .
# Sort imports
isort .
```

---
## High‑Level Architecture (from README)

```
Channels (CLI, Telegram)  ↓
   Agent  →  Memory (SQLite)  Provider (Ollama, Cloud)  Tools (shell, network)  Swarms (multi‑agent)
```

- **Memory**: Per‑user SQLite with FTS5, observations, relations.
- **Providers**: Local (Ollama, LM Studio) or Cloud (OpenAI, Anthropic, Gemini, Groq, OpenRouter).
- **Agent Swarms**: Four strategies – parallel, sequential, hierarchical, voting.
- **Channels**: Telegram & WhatsApp gateways (configurable, per‑user whitelist).
- **Toolbox**: Dynamic Python tools with documentation, stored in `~/.myclaw/TOOLBOX/`.
- **Knowledge Base**: Markdown notes with YAML frontmatter, stored in `~/.myclaw/knowledge/{user_id}/`.

---
## Common Commands (CLI)

| Tool | Description |
|------|-------------|
| `shell(cmd)` | Execute a command from the allowlist (`ls`, `git`, `curl`, etc.) |
| `write_file(path, content)` | Write a file inside the workspace |
| `read_file(path)` | Read a file (validated against path traversal) |
| `delegate(agent, task)` | Send a task to another agent |
| `register_tool(name, code, docs)` | Add a new tool to TOOLBOX |
| `swarm_create(name, strategy, workers, coordinator, aggregation)` | Create a new swarm |
| `swarm_assign(swarm_id, task)` | Execute a task in a swarm |
| `write_to_knowledge(title, content)` | Save a note to the knowledge base |
| `search_knowledge(query)` | Search notes with FTS5 |

## Building & Packaging (Future)

- No dedicated build script currently. When packaging, ensure:
  1. Include the `myclaw/` package and all sub‑modules.
  2. Ship `cli.py`, `onboard.py`, and `requirements.txt`.
  3. Document environment variable overrides in `myclaw/config.py` (`ENV_OVERRIDES` mapping).

---
## Guidelines for Future Developers

1. **Follow the existing pattern for new tools**:
   - Provide clear documentation.
   - Include `try/except` and log errors.
   - Register the tool with a unique name.
2. **When adding a new provider**:
   - Add the provider to `ProvidersConfig` in `myclaw/config.py`.
   - Implement the provider client in `myclaw/provider.py` with a uniform `chat()` method.
   - Test with both local and cloud endpoints.
3. **Agent Swarms**:
   - Use the `AgentDiscovery` class to suggest agents for a task.
   - Keep the number of workers per swarm bounded by the `max_concurrent_swarms` setting.
4. **Security**:
   - All file operations go through `validate_path()` – do not bypass it.
   - Shell commands must be whitelisted; avoid dynamic command construction.
5. **Testing**:
   - Tests are in `tests/` and use `pytest`. Write unit tests for new tools and integration tests for swarms.
   - Use `coverage` to ensure new code is well‑covered.

---
## Recent Bug Fixes (2026-04-13)

1. **Knowledge Researcher Import Fix** (`myclaw/knowledge/researcher.py`)
   - Fixed `ImportError` for non-existent `KBStorage` class
   - Uses correct imports: `KnowledgeDB` from `.db`, `write_note` from `.storage`

2. **Agent UnboundLocalError Fix** (`myclaw/agent.py`)
   - Fixed `UnboundLocalError` for `time` variable in `think()` method
   - Removed inner `import time` that shadowed module-level import

3. **OpenAI Tool Message Validation Fix** (`myclaw/provider.py`, `myclaw/agent.py`)
   - Fixed `BadRequestError: 400` for both single-tool and parallel multi-tool execution
   - **`agent.py`:** Replaced aggregated tool message with individual `role: "tool"` messages per `tool_call_id`
   - **`provider.py`:** Rewrote `_sanitize_messages_for_openai()` to track multi-message tool blocks and added `_ensure_tool_messages()` as a safety net for missing tool responses
   - Tool execution flows (`browse`, `shell`, `search_knowledge`, etc.) now work correctly

## Reporting Issues / Contributing

- Open a GitHub issue for bugs or feature requests.
- Pull requests are welcome; keep changes focused (e.g., fix a bug, add a tool, improve a swarm strategy).
- Follow the project's coding style: use type hints, keep functions under 100 lines, and add docstrings.

---
*Generated on 2026-04-05*
*Last updated: 2026-04-13 (Bug fixes applied)*
