"""Abstract base class for backend execution environments."""

import logging
from abc import ABC, abstractmethod
from typing import Optional, Tuple, List

logger = logging.getLogger(__name__)


class AbstractBackend(ABC):
    """Abstract base class for terminal backend implementations.
    
    All backend implementations (local, docker, ssh, wsl2) must inherit
    from this class and implement the required methods.
    """

    def __init__(self, config: Optional[dict] = None):
        """Initialize backend with optional configuration.
        
        Args:
            config: Backend-specific configuration dict
        """
        self.config = config or {}
        self._available = None

    @abstractmethod
    async def execute(self, command: str, timeout: int = 30) -> Tuple[str, int]:
        """Execute a command and return output.
        
        Args:
            command: Shell command to execute
            timeout: Timeout in seconds (default: 30)
            
        Returns:
            Tuple of (stdout/stderr output, exit code)
        """
        pass

    @abstractmethod
    async def upload(self, local_path: str, remote_path: str) -> bool:
        """Upload a file to the remote environment.
        
        Args:
            local_path: Path to local file
            remote_path: Destination path on remote
            
        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def download(self, remote_path: str, local_path: str) -> bool:
        """Download a file from the remote environment.
        
        Args:
            remote_path: Path to remote file
            local_path: Destination path locally
            
        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    def get_type(self) -> str:
        """Get the backend type identifier.
        
        Returns:
            String like 'local', 'docker', 'ssh', 'wsl2'
        """
        pass

    def is_available(self) -> bool:
        """Check if this backend is available on the current system.
        
        Returns:
            True if backend can be used, False otherwise
        """
        if self._available is None:
            self._available = self._check_availability()
        return self._available

    @abstractmethod
    def _check_availability(self) -> bool:
        """Internal check for backend availability.
        
        Implementations should perform the actual availability check
        (e.g., check if docker is installed, SSH is configured, etc.)
        
        Returns:
            True if backend is available
        """
        pass

    def get_info(self) -> dict:
        """Get backend information and status.
        
        Returns:
            Dict with backend metadata
        """
        return {
            "type": self.get_type(),
            "available": self.is_available(),
            "config": self.config
        }


class BackendRegistry:
    """Registry for managing available backends."""
    
    _backends: List[AbstractBackend] = []
    
    @classmethod
    def register(cls, backend: AbstractBackend) -> None:
        """Register a backend.
        
        Args:
            backend: Backend instance to register
        """
        if backend not in cls._backends:
            cls._backends.append(backend)
            logger.info(f"Backend registered: {backend.get_type()}")
    
    @classmethod
    def get_all(cls) -> List[AbstractBackend]:
        """Get all registered backends.
        
        Returns:
            List of backend instances
        """
        return cls._backends.copy()
    
    @classmethod
    def get_available(cls) -> List[AbstractBackend]:
        """Get all available backends.
        
        Returns:
            List of available backend instances
        """
        return [b for b in cls._backends if b.is_available()]
    
    @classmethod
    def get_by_type(cls, backend_type: str) -> Optional[AbstractBackend]:
        """Get a backend by type identifier.
        
        Args:
            backend_type: Type string ('local', 'docker', 'ssh', 'wsl2')
            
        Returns:
            Backend instance or None if not found
        """
        for backend in cls._backends:
            if backend.get_type() == backend_type:
                return backend
        return None
    
    @classmethod
    def clear(cls) -> None:
        """Clear all registered backends."""
        cls._backends.clear()