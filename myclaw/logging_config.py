"""
Standardized logging configuration for MyClaw.

This module provides consistent logging format across all MyClaw components
with structured logging support for better debugging and log analysis.

Log Format:
    [TIMESTAMP] [LEVEL] [COMPONENT] MESSAGE [KEY=VALUE ...]

Example:
    [2026-03-19T10:30:45.123Z] [INFO] [AGENT] Processing message user=alice [model=llama3.2]
    [2026-03-19T10:30:46.456Z] [ERROR] [PROVIDER] Request failed error=timeout [provider=ollama]
"""

import logging
import sys
import os
from datetime import datetime, timezone
from typing import Optional, Any, Dict
from functools import wraps
import json


# Log level colors for console output
LOG_COLORS = {
    'DEBUG': '\033[36m',    # Cyan
    'INFO': '\033[32m',     # Green
    'WARNING': '\033[33m',  # Yellow
    'ERROR': '\033[31m',    # Red
    'CRITICAL': '\033[35m', # Magenta
    'RESET': '\033[0m',     # Reset
}

# Component names for different modules
COMPONENT_NAMES = {
    'myclaw': 'CLAW',
    'myclaw.agent': 'AGENT',
    'myclaw.provider': 'PROVIDER',
    'myclaw.memory': 'MEMORY',
    'myclaw.knowledge': 'KNOWLEDGE',
    'myclaw.swarm': 'SWARM',
    'myclaw.tools': 'TOOLS',
    'myclaw.config': 'CONFIG',
    'myclaw.channels': 'CHANNEL',
    'myclaw.channels.telegram': 'TELEGRAM',
    'myclaw.channels.whatsapp': 'WHATSAPP',
    'myclaw.exceptions': 'ERROR',
}


class StructuredFormatter(logging.Formatter):
    """
    Custom formatter that produces structured, consistent log output.
    
    Supports both console (human-readable) and JSON (machine-parseable) formats.
    """
    
    def __init__(self, use_json: bool = False, use_color: bool = True):
        super().__init__()
        self.use_json = use_json
        self.use_color = use_color and sys.stdout.isatty()
    
    def format(self, record: logging.LogRecord) -> str:
        # Get timestamp in ISO 8601 format
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        
        # Get component name from module
        component = COMPONENT_NAMES.get(record.module, record.module.upper()[:8])
        
        # Build the message
        if self.use_json:
            return self._format_json(record, timestamp, component)
        else:
            return self._format_console(record, timestamp, component)
    
    def _format_json(self, record: logging.LogRecord, timestamp: str, component: str) -> str:
        """Format log record as JSON."""
        log_obj = {
            'timestamp': timestamp,
            'level': record.levelname,
            'component': component,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_obj['exception'] = self.formatException(record.exc_info)
        
        # Add extra fields
        if hasattr(record, 'extra_fields'):
            log_obj.update(record.extra_fields)
        
        return json.dumps(log_obj)
    
    def _format_console(self, record: logging.LogRecord, timestamp: str, component: str) -> str:
        """Format log record for console output."""
        # Add color codes
        level = record.levelname
        if self.use_color and level in LOG_COLORS:
            level_str = f"{LOG_COLORS[level]}{level}{LOG_COLORS['RESET']}"
        else:
            level_str = level
        
        # Build base message
        parts = [
            f"[{timestamp}]",
            f"[{level_str}]",
            f"[{component}]",
            record.getMessage(),
        ]
        
        # Add extra fields if present
        if hasattr(record, 'extra_fields') and record.extra_fields:
            extra_parts = [f"{k}={v}" for k, v in record.extra_fields.items()]
            parts.append("[" + " ".join(extra_parts) + "]")
        
        msg = " ".join(parts)
        
        # Add exception info if present
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        
        return msg


class LogContext:
    """
    Context manager for adding extra fields to log records.
    
    Usage:
        with LogContext(user_id="alice", request_id="123"):
            logger.info("Processing request")  # Logs with extra fields
    """
    
    def __init__(self, **kwargs: Any):
        self.fields = kwargs
        self.old_factory = None
    
    def __enter__(self) -> 'LogContext':
        self.old_factory = logging.getLogRecordFactory()
        
        def record_factory(*args, **kwargs):
            record = self.old_factory(*args, **kwargs)
            record.extra_fields = self.fields
            return record
        
        logging.setLogRecordFactory(record_factory)
        return self
    
    def __exit__(self, *args):
        logging.setLogRecordFactory(self.old_factory)


def get_logger(
    name: str,
    level: Optional[int] = None,
    use_json: bool = False,
    use_color: bool = True
) -> logging.Logger:
    """
    Get a configured logger with standardized format.
    
    Args:
        name: Logger name (typically __name__)
        level: Logging level (default: INFO)
        use_json: Output logs as JSON
        use_color: Use colored output for console
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    # Set level from environment if not provided
    if level is None:
        env_level = os.environ.get('MYCLAW_LOG_LEVEL', 'INFO')
        level = getattr(logging, env_level.upper(), logging.INFO)
    
    logger.setLevel(level)
    
    # Only add handler if not already configured
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter(use_json=use_json, use_color=use_color))
        logger.addHandler(handler)
    
    return logger


def configure_logging(
    level: Optional[int] = None,
    use_json: bool = False,
    use_color: bool = True,
    log_file: Optional[str] = None
) -> None:
    """
    Configure global logging for MyClaw.
    
    Args:
        level: Global logging level
        use_json: Use JSON format for all logs
        use_color: Use colored output
        log_file: Optional file path to also log to
    """
    # Set level from environment if not provided
    if level is None:
        env_level = os.environ.get('MYCLAW_LOG_LEVEL', 'INFO')
        level = getattr(logging, env_level.upper(), logging.INFO)
    
    # Check for JSON logging from environment
    use_json = use_json or os.environ.get('MYCLAW_LOG_JSON', '').lower() == 'true'
    
    # Configure root logger
    root_logger = logging.getLogger('myclaw')
    root_logger.setLevel(level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(StructuredFormatter(use_json=use_json, use_color=use_color))
    root_logger.addHandler(console_handler)
    
    # File handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        # Always use JSON for file logs
        file_handler.setFormatter(StructuredFormatter(use_json=True, use_color=False))
        root_logger.addHandler(file_handler)
    
    # Set third-party loggers to WARNING to reduce noise
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('anthropic').setLevel(logging.WARNING)


# Convenience function for audit logging
def audit_log(
    logger: logging.Logger,
    action: str,
    user_id: Optional[str] = None,
    **kwargs: Any
) -> None:
    """
    Log an audit event with structured data.
    
    Args:
        logger: Logger instance
        action: Action being performed (e.g., "user_login", "file_write")
        user_id: Optional user ID for user-specific actions
        **kwargs: Additional structured data to log
    """
    extra = {'action': action, 'audit': True}
    if user_id:
        extra['user_id'] = user_id
    extra.update(kwargs)
    
    with LogContext(**extra):
        logger.info(f"AUDIT: {action}")


# Performance logging decorator
def log_performance(logger: logging.Logger):
    """
    Decorator to log function execution time.
    
    Usage:
        @log_performance(logger)
        def my_function():
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            import time
            start = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start
                logger.debug(
                    f"Function {func.__name__} completed",
                    extra={'extra_fields': {'duration_ms': round(duration * 1000, 2)}}
                )
                return result
            except Exception as e:
                duration = time.time() - start
                logger.error(
                    f"Function {func.__name__} failed after {duration:.2f}s: {e}",
                    extra={'extra_fields': {'duration_ms': round(duration * 1000, 2)}}
                )
                raise
        return wrapper
    return decorator


# Export commonly used items
__all__ = [
    'get_logger',
    'configure_logging',
    'LogContext',
    'audit_log',
    'log_performance',
    'StructuredFormatter',
]
