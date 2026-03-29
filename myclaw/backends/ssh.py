"""SSH backend - Execute commands over SSH connection."""

import asyncio
import logging
import shlex
from pathlib import Path
from typing import Tuple

from .base import AbstractBackend

logger = logging.getLogger(__name__)


class SSHBackend(AbstractBackend):
    """SSH remote execution backend."""

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.host = config.get("host", "localhost") if config else "localhost"
        self.user = config.get("user", "") if config else ""
        self.port = config.get("port", 22) if config else 22
        self.key_path = config.get("key_path", "") if config else ""
        self.password = config.get("password", "") if config else ""

    async def execute(self, command: str, timeout: int = 30) -> Tuple[str, int]:
        """Execute command over SSH.
        
        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (output, exit_code)
        """
        try:
            ssh_cmd = self._build_ssh_command(command)
            
            process = await asyncio.create_subprocess_shell(
                ssh_cmd,
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
                return f"Error: SSH command timed out after {timeout} seconds", -1
                
        except Exception as e:
            logger.error(f"SSH execution error: {e}")
            return f"Error: {e}", -1

    def _build_ssh_command(self, command: str) -> str:
        """Build SSH command with options."""
        user_host = f"{self.user}@{self.host}" if self.user else self.host
        parts = ["ssh", "-p", str(self.port)]
        
        if self.key_path:
            parts.extend(["-i", self.key_path])
        
        parts.extend(["-o", "StrictHostKeyChecking=no"])
        parts.extend(["-o", "BatchMode=yes"])
        
        parts.append(user_host)
        parts.append(shlex.quote(command))
        
        return " ".join(parts)

    async def upload(self, local_path: str, remote_path: str) -> bool:
        """Upload file via SCP.
        
        Args:
            local_path: Source path
            remote_path: Destination path on remote
            
        Returns:
            True if successful
        """
        try:
            scp_cmd = self._build_scp_command(local_path, remote_path)
            
            proc = await asyncio.create_subprocess_shell(
                scp_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
            
        except Exception as e:
            logger.error(f"SCP upload error: {e}")
            return False

    async def download(self, remote_path: str, local_path: str) -> bool:
        """Download file via SCP.
        
        Args:
            remote_path: Source path on remote
            local_path: Destination path
            
        Returns:
            True if successful
        """
        try:
            scp_cmd = self._build_scp_command(remote_path, local_path, download=True)
            
            proc = await asyncio.create_subprocess_shell(
                scp_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
            
        except Exception as e:
            logger.error(f"SCP download error: {e}")
            return False

    def _build_scp_command(self, src: str, dst: str, download: bool = False) -> str:
        """Build SCP command."""
        user_host = f"{self.user}@{self.host}" if self.user else self.host
        
        parts = ["scp", "-P", str(self.port), "-o", "StrictHostKeyChecking=no"]
        
        if self.key_path:
            parts.extend(["-i", self.key_path])
        
        if download:
            parts.extend([f"{user_host}:{src}", dst])
        else:
            parts.extend([src, f"{user_host}:{dst}"])
        
        return " ".join(parts)

    def get_type(self) -> str:
        """Get backend type."""
        return "ssh"

    def _check_availability(self) -> bool:
        """Check if SSH is available."""
        try:
            proc = asyncio.run(asyncio.create_subprocess_shell(
                "which ssh",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ))
            asyncio.run(proc.communicate())
            return proc.returncode == 0
        except Exception:
            return False