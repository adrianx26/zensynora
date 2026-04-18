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
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install in editable mode
pip install -e .

# Verify installation
zensynora --help
```

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
│   ├── gateway.py          # Telegram/WhatsApp gateways
│   ├── memory.py           # SQLite memory system
│   ├── provider.py         # LLM provider abstraction
│   ├── tools.py            # Built-in tool definitions
│   ├── channels/           # Messaging channel implementations
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
pip install -e ".[dev]"      # Installs ruff, black, isort, pre-commit, pytest
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

## How to Add a New Tool / Skill

Tools are the primary way to extend ZenSynora's capabilities.

### 1. Define the Tool Function
Create a new function in `myclaw/tools.py` (or a new file if it's a large module):

```python
from myclaw.exceptions import ToolError

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
        raise ToolError(f"my_new_tool failed: {e}")
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
Create tests in `tests/test_tools.py` or a new test file:

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
# Run all tests
pytest

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
