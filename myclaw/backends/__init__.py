"""Backends package - Cross-platform terminal execution abstraction."""

from .base import AbstractBackend
from .local import LocalBackend
from .docker import DockerBackend
from .ssh import SSHBackend
from .wsl2 import WSL2Backend
from .discover import discover_backends, get_default_backend

__all__ = [
    "AbstractBackend",
    "LocalBackend", 
    "DockerBackend",
    "SSHBackend",
    "WSL2Backend",
    "discover_backends",
    "get_default_backend",
]