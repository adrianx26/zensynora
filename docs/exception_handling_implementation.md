# Exception Handling Implementation

> Document created: 2026-03-19

## Overview

This document describes the comprehensive exception handling implementation added to the MyClaw framework. This addresses the "Specific exception handling" item from the Low Priority category in the planA.md implementation roadmap.

## Changes Made

### 1. Enhanced Exception Hierarchy (`myclaw/exceptions.py`)

The exception module was expanded from 8 exception classes to 35+ specific exception classes organized in a clear hierarchy:

```
MyClawError (base)
├── ConfigurationError
│   ├── ConfigValidationError
│   └── ConfigNotFoundError
├── ProviderError
│   ├── ProviderNotFoundError
│   ├── ProviderTimeoutError
│   ├── ProviderConnectionError
│   ├── ProviderAuthenticationError
│   └── ProviderRateLimitError
├── ToolExecutionError
│   ├── RateLimitExceededError
│   ├── ToolNotFoundError
│   ├── ToolValidationError
│   └── ToolPermissionError
├── MemoryError
│   ├── MemoryConnectionError
│   ├── MemoryQueryError
│   └── MemoryCleanupError
├── KnowledgeBaseError
│   ├── KnowledgeNotFoundError
│   ├── KnowledgeParseError
│   └── KnowledgeSyncError
├── AgentRoutingError
│   ├── AgentNotFoundError
│   └── AgentNotAvailableError
├── SwarmError
│   ├── SwarmNotFoundError
│   ├── SwarmTimeoutError
│   ├── SwarmValidationError
│   └── SwarmConcurrencyError
└── ChannelError
    ├── ChannelNotFoundError
    ├── ChannelAuthenticationError
    └── ChannelWebhookError
```

### 2. Enhanced Exception Features

All exceptions now include:
- **Custom message**: Detailed error description
- **Details dictionary**: Context-specific information (provider name, timeout values, file paths, etc.)
- **Rich `__str__` method**: Shows both message and details

Example usage:
```python
# Before
raise ValueError("openai.api_key is not set in config.")

# After
raise ProviderAuthenticationError(
    "openai.api_key is not set in config.",
    provider="openai"
)
# Output: "openai.api_key is not set in config. (provider=openai)"
```

### 3. Files Updated

| File | Changes |
|------|---------|
| `myclaw/exceptions.py` | Expanded from 8 to 35+ exception classes |
| `myclaw/tools.py` | Updated to use `ToolValidationError`, `ToolPermissionError` |
| `myclaw/provider.py` | Updated to use `ProviderTimeoutError`, `ProviderConnectionError`, `ProviderAuthenticationError`, `ProviderNotFoundError` |
| `myclaw/swarm/orchestrator.py` | Updated to use `SwarmError`, `SwarmNotFoundError`, `SwarmConcurrencyError`, `AgentNotFoundError` |
| `myclaw/swarm/models.py` | Updated to use `SwarmValidationError` |
| `myclaw/memory.py` | Updated to use `MemoryError` |
| `myclaw/knowledge/storage.py` | Updated to use `KnowledgeBaseError`, `KnowledgeParseError` |
| `myclaw/knowledge/db.py` | Updated to use `KnowledgeNotFoundError` |

## Benefits

1. **Better Error Messages**: Exceptions now include context-specific details for easier debugging
2. **Structured Error Handling**: Catch specific exceptions instead of generic ones
3. **Improved Debugging**: Details dictionary provides additional context (timeouts, paths, etc.)
4. **Consistent Error Format**: All MyClaw exceptions follow the same structure

## Usage Examples

### Catching Specific Exceptions

```python
from myclaw.exceptions import (
    ProviderError,
    ProviderTimeoutError,
    ProviderAuthenticationError
)

try:
    response = await provider.chat(messages)
except ProviderAuthenticationError as e:
    logger.error(f"API key issue: {e.message}")
    # Handle missing/invalid API key
except ProviderTimeoutError as e:
    logger.warning(f"Request timed out after {e.details.get('timeout_seconds')}s")
    # Retry with longer timeout
except ProviderError as e:
    logger.error(f"Provider error: {e.message}")
    # Handle generic provider error
```

### Raising Exceptions with Details

```python
from myclaw.exceptions import SwarmConcurrencyError

raise SwarmConcurrencyError(
    f"Maximum concurrent swarms ({max}) reached.",
    max_concurrent=max,
    current_count=current
)
```

## Backward Compatibility

The changes maintain backward compatibility:
- All new exceptions inherit from existing base classes (`MyClawError`, `ProviderError`, etc.)
- Code catching `ValueError`, `RuntimeError` etc. will still work (but should be updated)
- Error messages remain human-readable

## Testing

Run the test suite to verify:

```bash
python -m pytest tests/ -v
```
