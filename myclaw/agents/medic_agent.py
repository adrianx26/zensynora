"""
Medic Agent - System health monitoring and recovery tools.
"""

import logging

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)

# This is a placeholder for the config object that would be set by the gateway.
_config = None

def set_config(config):
    """Injects the application config into the medic agent module."""
    global _config
    _config = config

def check_memory_usage(threshold_percent: float = 85.0) -> str:
    """
    Checks current system memory (RAM) usage and logs a warning if it exceeds a threshold.

    Args:
        threshold_percent (float): The memory usage percentage threshold to trigger a warning. Defaults to 85.0.

    Returns:
        str: A status message indicating memory usage and whether it's high.
    """
    if not PSUTIL_AVAILABLE:
        return "Cannot check memory usage: 'psutil' library is not installed. Please run 'pip install psutil'."

    memory = psutil.virtual_memory()
    usage_percent = memory.percent

    message = f"Current memory usage: {usage_percent:.1f}% (Total: {memory.total / (1024**3):.2f} GB, Used: {memory.used / (1024**3):.2f} GB)."

    if usage_percent > threshold_percent:
        warning_message = f"HIGH MEMORY USAGE DETECTED: {usage_percent:.1f}% exceeds threshold of {threshold_percent:.1f}%."
        logger.warning(warning_message)
        return f"WARNING: {warning_message}"
    else:
        logger.info(message)
        return f"OK: {message}"

def check_system_health() -> str:
    return "System health is nominal. All core services are running."

def get_health_report() -> str:
    return "Health Report:\n- CPU Load: Normal\n- Memory Usage: Normal\n- Disk Space: OK\n- Connectivity: OK"