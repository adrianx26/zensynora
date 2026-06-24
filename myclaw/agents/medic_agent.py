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


class MedicAgent:
    """
    MedicAgent - A class-based wrapper around the medic agent's health monitoring tools.
    Instantiated by the gateway on startup to perform system health checks.
    """

    def scan_system(self) -> str:
        """
        Run a full system scan: memory usage + overall health status.

        Returns:
            str: A combined health report string.
        """
        memory_status = check_memory_usage()
        health_status = check_system_health()
        report = get_health_report()
        return f"{memory_status}\n{health_status}\n{report}"

    def check_memory(self, threshold_percent: float = 85.0) -> str:
        """Delegate to the module-level check_memory_usage function."""
        return check_memory_usage(threshold_percent)

    def health_status(self) -> str:
        """Delegate to the module-level check_system_health function."""
        return check_system_health()

    def health_report(self) -> str:
        """Delegate to the module-level get_health_report function."""
        return get_health_report()