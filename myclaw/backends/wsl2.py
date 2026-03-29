"""WSL2 backend - Execute commands in WSL2 Linux environment."""

import asyncio
import logging
from pathlib import Path, PureWindowsPath
from typing import Tuple

from .base import AbstractBackend

logger = logging.getLogger(__name__)


class WSL2Backend(AbstractBackend):
    """WSL2 (Windows Subsystem for Linux) execution backend."""

    def __init__(self, config: dict = None):
        super().__init__(config)
        self.distro = config.get("distro", "Ubuntu") if config else "Ubuntu"
        self.wsl_cmd = f"wsl -d {self.distro}"

    async def execute(self, command: str, timeout: int = 30) -> Tuple[str, int]:
        """Execute command in WSL2.
        
        Args:
            command: Shell command to execute
            timeout: Timeout in seconds
            
        Returns:
            Tuple of (output, exit_code)
        """
        try:
            wsl_full_cmd = f"{self.wsl_cmd} sh -c {repr(command)}"
            
            process = await asyncio.create_subprocess_shell(
                wsl_full_cmd,
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
                return f"Error: WSL command timed out after {timeout} seconds", -1
                
        except Exception as e:
            logger.error(f"WSL2 execution error: {e}")
            return f"Error: {e}", -1

    async def upload(self, local_path: str, remote_path: str) -> bool:
        """Upload file to WSL2.
        
        Args:
            local_path: Source path (Windows path)
            remote_path: Destination path in WSL
            
        Returns:
            True if successful
        """
        try:
            wsl_path = self._convert_to_wsl_path(remote_path)
            await self.execute(f"mkdir -p {Path(wsl_path).parent}", timeout=10)
            
            wsl_cp = f"wsl -d {self.distro} cp {local_path.replace(chr(92), '/')} {wsl_path}"
            proc = await asyncio.create_subprocess_shell(
                wsl_cp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
            
        except Exception as e:
            logger.error(f"WSL2 upload error: {e}")
            return False

    async def download(self, remote_path: str, local_path: str) -> bool:
        """Download file from WSL2.
        
        Args:
            remote_path: Source path in WSL
            local_path: Destination path (Windows path)
            
        Returns:
            True if successful
        """
        try:
            wsl_path = self._convert_to_wsl_path(remote_path)
            wsl_cp = f"wsl -d {self.distro} cp {wsl_path} {local_path.replace(chr(92), '/')}"
            proc = await asyncio.create_subprocess_shell(
                wsl_cp,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            return proc.returncode == 0
            
        except Exception as e:
            logger.error(f"WSL2 download error: {e}")
            return False

    def _convert_to_wsl_path(self, windows_path: str) -> str:
        """Convert Windows path to WSL path."""
        p = Path(windows_path)
        return f"/mnt/{p.drive[0].lower()}/{str(p).replace(str(p.drive), '').replace(chr(92), '/').lstrip('/')}"

    def get_type(self) -> str:
        """Get backend type."""
        return "wsl2"

    def _check_availability(self) -> bool:
        """Check if WSL2 is available."""
        try:
            proc = asyncio.run(asyncio.create_subprocess_shell(
                "wsl --list",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            ))
            stdout, _ = asyncio.run(proc.communicate())
            return proc.returncode == 0 and self.distro.lower() in stdout.decode().lower()
        except Exception:
            return False