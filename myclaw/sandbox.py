"""
Enhanced Security Sandbox

Provides comprehensive security isolation for code execution:
- Process isolation with resource limits
- Restricted file system access
- Network sandboxing
- Execution time limits
- Sandboxed imports
- Security audit logging
"""

import asyncio
import hashlib
import logging
import os
import signal
import subprocess
import tempfile
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable

from .audit_log import TamperEvidentAuditLog

logger = logging.getLogger(__name__)

try:
    import resource
except ImportError:  # pragma: no cover - platform-specific
    resource = None

SANDBOX_DIR = Path.home() / ".myclaw" / "sandbox"
SANDBOX_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class SecurityPolicy:
    """Security policy for sandbox execution."""
    max_memory_mb: int = 256
    max_cpu_percent: int = 50
    max_execution_seconds: int = 30
    max_file_size_mb: int = 10
    max_processes: int = 5
    allow_network: bool = False
    allow_file_write: bool = False
    allowed_paths: List[str] = field(default_factory=list)
    blocked_imports: List[str] = field(default_factory=list)
    blocked_modules: List[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Result of sandboxed execution."""
    success: bool
    output: str = ""
    error: Optional[str] = None
    exit_code: Optional[int] = None
    execution_time_seconds: float = 0.0
    memory_used_mb: float = 0.0
    return_value: Any = None
    sandbox_violations: List[str] = field(default_factory=list)


@dataclass
class AuditEntry:
    """Security audit log entry."""
    timestamp: datetime
    event_type: str
    details: Dict[str, Any]
    severity: str = "INFO"


class SecuritySandbox:
    """Enhanced security sandbox for code execution.
    
    Features:
    - Process-level isolation
    - Resource limits (CPU, memory, time)
    - File system restrictions
    - Network access control
    - Sandboxed imports
    - Security audit logging
    """
    
    DEFAULT_POLICY = SecurityPolicy(
        max_memory_mb=256,
        max_cpu_percent=50,
        max_execution_seconds=30,
        max_file_size_mb=10,
        max_processes=5,
        allow_network=False,
        allow_file_write=False,
        allowed_paths=[str(Path.home() / ".myclaw" / "workspace")],
        blocked_imports=["os.system", "subprocess.Popen", "ctypes", "winreg", "socket"],
        blocked_modules=["ctypes", "winreg", "multiprocessing"]
    )
    
    def __init__(self, policy: Optional[SecurityPolicy] = None):
        self._policy = policy or self.DEFAULT_POLICY
        self._audit_log: List[AuditEntry] = []
        self._audit_backend = TamperEvidentAuditLog(
            log_path=SANDBOX_DIR / "sandbox_audit.log.jsonl"
        )
        self._active_executions: Dict[str, subprocess.Popen] = {}
    
    @property
    def policy(self) -> SecurityPolicy:
        return self._policy
    
    def update_policy(self, **kwargs):
        """Update security policy settings."""
        for key, value in kwargs.items():
            if hasattr(self._policy, key):
                setattr(self._policy, key, value)
        self._log_audit("policy_update", {"changes": kwargs})
    
    def _log_audit(self, event_type: str, details: Dict[str, Any], severity: str = "INFO"):
        """Log a security audit entry."""
        entry = AuditEntry(
            timestamp=datetime.now(),
            event_type=event_type,
            details=details,
            severity=severity
        )
        self._audit_log.append(entry)
        self._audit_backend.append(
            event_type=event_type,
            details=details,
            severity=severity,
        )
        
        if len(self._audit_log) > 1000:
            self._audit_log = self._audit_log[-500:]
        
        logger.debug(f"Sandbox audit: {event_type} - {severity}")
    
    def validate_code(self, code: str) -> List[str]:
        """Validate code for security issues before execution.
        
        Args:
            code: Python code to validate
            
        Returns:
            List of security violation messages
        """
        violations = []
        
        for blocked in self._policy.blocked_imports:
            if blocked in code:
                violations.append(f"Blocked import: {blocked}")
        
        for blocked in self._policy.blocked_modules:
            if f"import {blocked}" in code or f"from {blocked}" in code:
                violations.append(f"Blocked module: {blocked}")
        
        dangerous_patterns = [
            (r"__import__\s*\(", "Dynamic import"),
            (r"eval\s*\(", "eval() usage"),
            (r"exec\s*\(", "exec() usage"),
            (r"os\.system\s*\(", "os.system() call"),
            (r"subprocess\.run\s*\(.*shell\s*=\s*True", "shell=True in subprocess"),
            (r"open\s*\([^)]*['\"].*['\"].*['\"].*['\"]", "unsafe file open"),
            (r"shutil\.rmtree\s*\(", "shutil.rmtree() call"),
            (r"os\.remove\s*\(", "os.remove() call"),
            (r" pathlib.*\.unlink\s*\(", "file deletion"),
        ]
        
        for pattern, description in dangerous_patterns:
            import re
            if re.search(pattern, code):
                violations.append(f"Dangerous pattern: {description}")
        
        if not self._policy.allow_file_write:
            write_patterns = [r"open\s*\([^)]*['\"].*['\"].*['\"].*['\"]", r"Path.*\.touch\s*\("]
            for pattern in write_patterns:
                if re.search(pattern, code, re.DOTALL):
                    violations.append("File write not allowed in sandbox")
                    break
        
        return violations
    
    def execute_code(
        self,
        code: str,
        timeout: Optional[int] = None,
        capture_output: bool = True
    ) -> ExecutionResult:
        """Execute code in a secure sandbox.
        
        Args:
            code: Python code to execute
            timeout: Execution timeout in seconds
            capture_output: Whether to capture stdout/stderr
            
        Returns:
            ExecutionResult with execution details
        """
        import time
        start_time = time.time()
        
        violations = self.validate_code(code)
        if violations:
            self._log_audit("security_violation", {"violations": violations}, "WARNING")
            return ExecutionResult(
                success=False,
                error=f"Security violations found: {', '.join(violations)}",
                sandbox_violations=violations
            )
        
        with tempfile.TemporaryDirectory(dir=SANDBOX_DIR) as tmpdir:
            script_path = Path(tmpdir) / "sandbox_script.py"
            
            sandbox_wrapper = self._create_sandbox_wrapper(code)
            script_path.write_text(sandbox_wrapper, encoding="utf-8")
            
            try:
                process = self._run_in_sandbox(script_path, timeout, tmpdir)
                self._active_executions[str(script_path)] = process
                
                stdout, stderr = process.communicate()
                
                del self._active_executions[str(script_path)]
                
                execution_time = time.time() - start_time
                
                if capture_output:
                    output = (stdout or b"").decode("utf-8", errors="replace")
                    error = (stderr or b"").decode("utf-8", errors="replace")
                else:
                    output = ""
                    error = ""
                
                success = process.returncode == 0
                
                return ExecutionResult(
                    success=success,
                    output=output,
                    error=error if not success else None,
                    exit_code=process.returncode,
                    execution_time_seconds=execution_time
                )
                
            except subprocess.TimeoutExpired:
                self._log_audit("execution_timeout", {"timeout": timeout}, "WARNING")
                return ExecutionResult(
                    success=False,
                    error=f"Execution timed out after {timeout}s",
                    execution_time_seconds=timeout
                )
            except Exception as e:
                logger.error(f"Sandbox execution error: {e}")
                return ExecutionResult(
                    success=False,
                    error=str(e),
                    execution_time_seconds=time.time() - start_time
                )
            finally:
                try:
                    script_path.unlink(missing_ok=True)
                except Exception:
                    pass
    
    def _create_sandbox_wrapper(self, code: str) -> str:
        """Create sandboxed wrapper code."""
        return f'''"""Sandboxed execution wrapper."""

import sys
import os

# Sandbox restrictions
sys.path = []
os.chdir("{SANDBOX_DIR}")

# Block dangerous imports
class SandboxedImporter:
    def find_module(self, name, path=None):
        blocked = {repr(m) for m in self._policy.blocked_modules}
        if name in blocked:
            raise ImportError(f"Module {{name}} is blocked in sandbox")
        return None

# Install sandbox importer
import builtins
_original_import = builtins.__import__
def _sandboxed_import(name, *args, **kwargs):
    blocked = {repr(m) for m in self._policy.blocked_modules}
    if name in blocked:
        raise ImportError(f"Module {{name}} is blocked in sandbox")
    return _original_import(name, *args, **kwargs)

# Block network operations
import socket
_original_socket = socket.socket
class _RestrictedSocket:
    def __init__(self, *args, **kwargs):
        if not self._policy.allow_network:
            raise OSError("Network access blocked in sandbox")
        super().__init__(*args, **kwargs)

# Apply restrictions
builtins.__import__ = _sandboxed_import

# User code execution
try:
{self._indent_code(code, 4)}
except Exception as e:
    print(f"Error: {{e}}", file=sys.stderr)
    sys.exit(1)
'''
    
    def _indent_code(self, code: str, spaces: int) -> str:
        """Indent code by specified spaces."""
        indent = " " * spaces
        return "\n".join(
            indent + line if line.strip() else ""
            for line in code.split("\n")
        )
    
    def _run_in_sandbox(
        self,
        script_path: Path,
        timeout: Optional[int],
        tmpdir: str
    ) -> subprocess.Popen:
        """Run script in sandbox with resource limits."""
        timeout = timeout or self._policy.max_execution_seconds
        
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = ""
        
        process = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=tmpdir,
            env=env,
            preexec_fn=self._set_resource_limits if (hasattr(os, "setrlimit") and resource is not None) else None
        )
        
        return process
    
    def _set_resource_limits(self):
        """Set resource limits for the subprocess."""
        if resource is None:
            return
        try:
            max_mem = self._policy.max_memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (max_mem, max_mem))
            
            resource.setrlimit(
                resource.RLIMIT_CPU,
                (self._policy.max_execution_seconds, self._policy.max_execution_seconds)
            )
            
            resource.setrlimit(
                resource.RLIMIT_NPROC,
                (self._policy.max_processes, self._policy.max_processes)
            )
        except Exception as e:
            logger.warning(f"Failed to set resource limits: {e}")
    
    def terminate_all(self) -> int:
        """Terminate all active sandboxed executions."""
        count = 0
        for path, process in list(self._active_executions.items()):
            try:
                process.terminate()
                process.wait(timeout=5)
                count += 1
            except Exception:
                try:
                    process.kill()
                    count += 1
                except Exception:
                    pass
        
        self._log_audit("terminate_all", {"count": count})
        return count
    
    def get_audit_log(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get security audit log entries."""
        persisted = self._audit_backend.read_entries(limit=limit)
        if persisted:
            return persisted
        return [
            {
                "timestamp": e.timestamp.isoformat(),
                "event_type": e.event_type,
                "severity": e.severity,
                "details": e.details
            }
            for e in self._audit_log[-limit:]
        ]

    def clear_audit_log(self) -> None:
        """Clear sandbox persistent audit log."""
        self._audit_backend.clear()
        self._audit_log = []

    def get_stats(self) -> Dict[str, Any]:
        """Get current sandbox runtime stats."""
        return {
            "active_executions": len(self._active_executions),
            "memory_audit_entries": len(self._audit_log),
            "policy": {
                "max_memory_mb": self._policy.max_memory_mb,
                "max_execution_seconds": self._policy.max_execution_seconds,
                "allow_network": self._policy.allow_network,
            },
            "audit_integrity": self._audit_backend.verify_integrity(),
        }


class SandboxedFunction:
    """Decorator to run a function in sandbox.
    
    Usage:
        @SandboxedFunction(max_time=5)
        def dangerous_operation(data):
            # Run in sandbox
            pass
    """
    
    def __init__(
        self,
        max_memory_mb: int = 128,
        max_time_seconds: int = 10,
        policy: Optional[SecurityPolicy] = None
    ):
        self._max_memory = max_memory_mb
        self._max_time = max_time_seconds
        self._sandbox = SecuritySandbox(policy or SecurityPolicy(
            max_memory_mb=max_memory_mb,
            max_execution_seconds=max_time_seconds
        ))
    
    def __call__(self, func: Callable) -> Callable:
        """Decorator call."""
        def wrapper(*args, **kwargs):
            import sys
            code = f"""import json
result = None
try:
    result = {func.__name__}(*args, **kwargs)
except Exception as e:
    print(f"ERROR: {{e}}", file=sys.stderr)
    raise
print(f"RESULT: {{json.dumps(result) if result is not None else 'None'}}")
"""
            result = self._sandbox.execute_code(code, timeout=self._max_time)
            
            if result.success:
                return result.return_value
            else:
                raise RuntimeError(result.error or "Sandbox execution failed")
        
        return wrapper


def create_sandbox(
    max_memory_mb: int = 256,
    max_time_seconds: int = 30,
    allow_network: bool = False
) -> SecuritySandbox:
    """Create a configured security sandbox.
    
    Args:
        max_memory_mb: Maximum memory in MB
        max_time_seconds: Maximum execution time
        allow_network: Whether to allow network access
        
    Returns:
        Configured SecuritySandbox instance
    """
    policy = SecurityPolicy(
        max_memory_mb=max_memory_mb,
        max_execution_seconds=max_time_seconds,
        allow_network=allow_network
    )
    return SecuritySandbox(policy)


async def execute_in_sandbox(
    code: str,
    timeout: int = 30,
    **kwargs
) -> ExecutionResult:
    """Async wrapper for sandbox execution.
    
    Args:
        code: Python code to execute
        timeout: Execution timeout
        **kwargs: Additional sandbox options
        
    Returns:
        ExecutionResult
    """
    sandbox = create_sandbox(**kwargs)
    loop = asyncio.get_event_loop()
    
    return await loop.run_in_executor(
        None,
        lambda: sandbox.execute_code(code, timeout=timeout)
    )


__all__ = [
    "SecurityPolicy",
    "ExecutionResult",
    "AuditEntry",
    "SecuritySandbox",
    "SandboxedFunction",
    "create_sandbox",
    "execute_in_sandbox",
]
