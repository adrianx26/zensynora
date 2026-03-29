"""Backend discovery and selection utilities."""

import logging
from typing import List, Optional

from .base import AbstractBackend, BackendRegistry
from .local import LocalBackend
from .docker import DockerBackend
from .ssh import SSHBackend
from .wsl2 import WSL2Backend

logger = logging.getLogger(__name__)

_default_backend_type = "local"


def discover_backends() -> List[AbstractBackend]:
    """Discover all available backends on the system.
    
    Returns:
        List of available backend instances
    """
    backends = []
    
    local = LocalBackend()
    if local.is_available():
        backends.append(local)
        BackendRegistry.register(local)
        logger.info("Discovered local backend")
    
    docker = DockerBackend()
    if docker.is_available():
        backends.append(docker)
        BackendRegistry.register(docker)
        logger.info("Discovered Docker backend")
    
    ssh = SSHBackend()
    if ssh.is_available():
        backends.append(ssh)
        BackendRegistry.register(ssh)
        logger.info("Discovered SSH backend")
    
    wsl2 = WSL2Backend()
    if wsl2.is_available():
        backends.append(wsl2)
        BackendRegistry.register(wsl2)
        logger.info("Discovered WSL2 backend")
    
    return backends


def get_default_backend(config: Optional[dict] = None) -> Optional[AbstractBackend]:
    """Get the default backend based on configuration.
    
    Args:
        config: Optional configuration dict
        
    Returns:
        Default backend instance or None
    """
    global _default_backend_type
    
    if config:
        backend_type = config.get("default_backend", "local")
        _default_backend_type = backend_type
    
    discovered = BackendRegistry.get_available()
    
    for backend in discovered:
        if backend.get_type() == _default_backend_type:
            return backend
    
    for backend in discovered:
        if backend.get_type() == "local":
            return backend
    
    return discovered[0] if discovered else None


def set_default_backend(backend_type: str) -> bool:
    """Set the default backend type.
    
    Args:
        backend_type: Type identifier ('local', 'docker', 'ssh', 'wsl2')
        
    Returns:
        True if successful
    """
    global _default_backend_type
    valid_types = {"local", "docker", "ssh", "wsl2"}
    
    if backend_type not in valid_types:
        logger.error(f"Invalid backend type: {backend_type}")
        return False
    
    _default_backend_type = backend_type
    return True


def get_backend_info() -> dict:
    """Get information about all available backends.
    
    Returns:
        Dict with backend status information
    """
    backends = BackendRegistry.get_all()
    
    return {
        "available": [b.get_info() for b in backends if b.is_available()],
        "unavailable": [b.get_info() for b in backends if not b.is_available()],
        "default": _default_backend_type,
        "total_count": len(backends)
    }


def execute_with_backend(command: str, backend_type: Optional[str] = None, 
                         config: Optional[dict] = None, timeout: int = 30):
    """Execute a command with a specific or default backend.
    
    Args:
        command: Shell command to execute
        backend_type: Specific backend type (uses default if None)
        config: Backend configuration
        timeout: Execution timeout in seconds
        
    Returns:
        Tuple of (output, exit_code, backend_type) or (None, None, None) if unavailable
    """
    import asyncio
    
    if backend_type:
        backend = BackendRegistry.get_by_type(backend_type)
    else:
        backend = get_default_backend(config)
    
    if not backend or not backend.is_available():
        logger.error(f"Backend '{backend_type or _default_backend_type}' not available")
        return None, None, None
    
    try:
        output, exit_code = asyncio.run(backend.execute(command, timeout))
        return output, exit_code, backend.get_type()
    except Exception as e:
        logger.error(f"Backend execution error: {e}")
        return f"Error: {e}", -1, backend.get_type()