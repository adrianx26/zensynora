"""
SSH backend - Execute commands over SSH connection using Paramiko.
Supports both key-based and password-based authentication.
"""

import asyncio
import logging
import os
import paramiko
from pathlib import Path
from typing import Tuple, Optional
from .base import AbstractBackend

logger = logging.getLogger(__name__)


class SSHBackend(AbstractBackend):
    """SSH remote execution backend using Paramiko."""

    def __init__(self, config: dict = None):
        super().__init__(config)
        # Handle both dict and Pydantic model
        if hasattr(config, "model_dump"):
            cfg = config.model_dump()
        else:
            cfg = config or {}

        self.host = cfg.get("host", "localhost")
        self.user = cfg.get("user", "root")
        self.port = cfg.get("port", 22)
        self.key_path = cfg.get("key_path", "")
        self.password = cfg.get("password", "")

        # Resolve password from SecretStr if needed
        if hasattr(self.password, "get_secret_value"):
            self.password = self.password.get_secret_value()

        self._client: Optional[paramiko.SSHClient] = None

    def _get_client(self) -> paramiko.SSHClient:
        """Create and connect SSH client."""
        client = paramiko.SSHClient()
        # SECURITY FIX (2026-04-23): Reject unknown host keys instead of auto-accepting.
        # AutoAddPolicy() accepts ANY host key, making connections vulnerable to MITM.
        # We load the user's known_hosts file and reject keys not present in it.
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        known_hosts = Path.home() / ".ssh" / "known_hosts"
        if known_hosts.exists():
            try:
                client.load_host_keys(str(known_hosts))
            except Exception as e:
                logger.warning(f"Could not load known_hosts ({known_hosts}): {e}")
        else:
            logger.warning(
                "No known_hosts file found. SSH connections may fail. "
                "Run 'ssh <host>' once manually to populate known_hosts."
            )

        try:
            if self.key_path:
                key_path = Path(self.key_path).expanduser()
                if key_path.exists():
                    client.connect(
                        hostname=self.host,
                        port=self.port,
                        username=self.user,
                        key_filename=str(key_path),
                        timeout=10,
                        look_for_keys=False,
                    )
                    return client

            # Fallback to password
            if self.password:
                client.connect(
                    hostname=self.host,
                    port=self.port,
                    username=self.user,
                    password=self.password,
                    timeout=10,
                )
                return client

            # Final attempt: default keys
            client.connect(hostname=self.host, port=self.port, username=self.user, timeout=10)
            return client

        except Exception as e:
            logger.error(f"SSH connection failed to {self.host}: {e}")
            raise

    async def execute(self, command: str, timeout: int = 30) -> Tuple[str, int]:
        """Execute command over SSH."""
        return await asyncio.to_thread(self._execute_sync, command, timeout)

    def _execute_sync(self, command: str, timeout: int = 30) -> Tuple[str, int]:
        """Synchronous execution wrapper for Paramiko."""
        client = None
        try:
            client = self._get_client()
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)

            output = stdout.read().decode("utf-8", errors="replace")
            error = stderr.read().decode("utf-8", errors="replace")
            exit_code = stdout.channel.recv_exit_status()

            combined_output = output + error
            return combined_output.strip(), exit_code

        except Exception as e:
            logger.error(f"SSH execution error on {self.host}: {e}")
            return f"Error: {e}", -1
        finally:
            if client:
                client.close()

    async def upload(self, local_path: str, remote_path: str) -> bool:
        """Upload file via SFTP."""
        return await asyncio.to_thread(self._upload_sync, local_path, remote_path)

    def _upload_sync(self, local_path: str, remote_path: str) -> bool:
        client = None
        try:
            client = self._get_client()
            sftp = client.open_sftp()
            sftp.put(local_path, remote_path)
            sftp.close()
            return True
        except Exception as e:
            logger.error(f"SFTP upload error: {e}")
            return False
        finally:
            if client:
                client.close()

    async def download(self, remote_path: str, local_path: str) -> bool:
        """Download file via SFTP."""
        return await asyncio.to_thread(self._download_sync, remote_path, local_path)

    def _download_sync(self, remote_path: str, local_path: str) -> bool:
        client = None
        try:
            client = self._get_client()
            sftp = client.open_sftp()
            sftp.get(remote_path, local_path)
            sftp.close()
            return True
        except Exception as e:
            logger.error(f"SFTP download error: {e}")
            return False
        finally:
            if client:
                client.close()

    def get_type(self) -> str:
        return "ssh"

    def _check_availability(self) -> bool:
        """Check if library is available and host is configured."""
        return bool(self.host and (self.key_path or self.password))
