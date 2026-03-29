"""Local backend - Direct shell execution on the local system."""

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Tuple

from .base import AbstractBackend

logger = logging.getLogger(__name__)


class LocalBackend(AbstractBackend):
    """Local shell execution backend."""

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.workspace = Path.home() / ".myclaw" / "workspace"

    async def execute(self, command: str, timeout: int = 30) -> Tuple[str, int]:
        """Execute command locally.
        
        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (output, exit_code)
        """
        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace
            )
            
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
                output = stdout.decode() + stderr.decode()
                return output.strip(), process.returncode
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return f"Error: Command timed out after {timeout} seconds", -1
                
        except Exception as e:
            logger.error(f"Local execution error: {e}")
            return f"Error: {e}", -1

    async def upload(self, local_path: str, remote_path: str) -> bool:
        """Upload file to local workspace.
        
        Args:
            local_path: Source path
            remote_path: Destination path
            
        Returns:
            True if successful
        """
        try:
            src = Path(local_path)
            dst = Path(remote_path)
            
            if not src.exists():
                return False
            
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return True
            
        except Exception as e:
            logger.error(f"Upload error: {e}")
            return False

    async def download(self, remote_path: str, local_path: str) -> bool:
        """Download file from local workspace.
        
        Args:
            remote_path: Source path
            local_path: Destination path
            
        Returns:
            True if successful
        """
        try:
            src = Path(remote_path)
            dst = Path(local_path)
            
            if not src.exists():
                return False
            
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            return True
            
        except Exception as e:
            logger.error(f"Download error: {e}")
            return False

    def get_type(self) -> str:
        """Get backend type."""
        return "local"

    def _check_availability(self) -> bool:
        """Check if local execution is available."""
        return True