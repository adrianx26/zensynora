"""Docker backend - Execute commands in Docker containers."""

import asyncio
import logging
from pathlib import Path
from typing import Tuple

from .base import AbstractBackend

logger = logging.getLogger(__name__)


class DockerBackend(AbstractBackend):
    """Docker container execution backend."""

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.container = config.get("container", "zensynora") if config else "zensynora"
        self.image = config.get("image", "zensynora:latest") if config else "zensynora:latest"

    async def execute(self, command: str, timeout: int = 30) -> Tuple[str, int]:
        """Execute command in Docker container.
        
        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (output, exit_code)
        """
        try:
            docker_cmd = f"docker exec {self.container} sh -c {repr(command)}"
            
            process = await asyncio.create_subprocess_shell(
                docker_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                output = stdout.decode() + stderr.decode()
                return output.strip(), process.returncode
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return f"Error: Docker command timed out after {timeout} seconds", -1
                
        except Exception as e:
            logger.error(f"Docker execution error: {e}")
            return f"Error: {e}", -1

    async def upload(self, local_path: str, remote_path: str) -> bool:
        """Upload file to Docker container.
        
        Args:
            local_path: Source path
            remote_path: Destination path in container
            
        Returns:
            True if successful
        """
        try:
            result = await self.execute(f"mkdir -p {Path(remote_path).parent}", timeout=10)
            if result[1] != 0:
                return False
            
            docker_cp = f"docker cp {local_path} {self.container}:{remote_path}"
            proc = await asyncio.create_subprocess_shell(
                docker_cp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
            
        except Exception as e:
            logger.error(f"Docker upload error: {e}")
            return False

    async def download(self, remote_path: str, local_path: str) -> bool:
        """Download file from Docker container.
        
        Args:
            remote_path: Source path in container
            local_path: Destination path locally
            
        Returns:
            True if successful
        """
        try:
            docker_cp = f"docker cp {self.container}:{remote_path} {local_path}"
            proc = await asyncio.create_subprocess_shell(
                docker_cp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
            
        except Exception as e:
            logger.error(f"Docker download error: {e}")
            return False

    def get_type(self) -> str:
        """Get backend type."""
        return "docker"

    def _check_availability(self) -> bool:
        """Check if Docker is available."""
        try:
            proc = asyncio.run(asyncio.create_subprocess_shell(
                "docker --version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ))
            asyncio.run(proc.communicate())
            return proc.returncode == 0
        except Exception:
            return False