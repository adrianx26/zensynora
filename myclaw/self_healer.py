"""
Self-Healing Code Module

Automatically detects and fixes errors in generated or executed code.
Uses LLM-based analysis and pattern-based fixes to repair common issues.

Features:
- Runtime error detection and recovery
- Syntax error auto-correction
- Common bug pattern detection
- Safe rollback mechanisms
- Error recovery strategies
"""

import asyncio
import logging
import re
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ErrorInfo:
    """Information about an error."""
    error_type: str
    error_message: str
    traceback: str
    line_number: Optional[int] = None
    file_name: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.now)
    context: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FixResult:
    """Result of an error fix attempt."""
    success: bool
    original_error: str
    fixed_code: Optional[str] = None
    fix_applied: str = ""
    recovery_strategy: str = ""
    attempts: int = 0
    new_error: Optional[str] = None


@dataclass
class RecoveryStrategy:
    """A recovery strategy for handling errors."""
    name: str
    description: str
    applies_to: List[str]
    priority: int = 0
    
    def can_handle(self, error_type: str) -> bool:
        return error_type in self.applies_to


class ErrorPatternDatabase:
    """Database of known error patterns and their fixes."""
    
    SYNTAX_PATTERNS = {
        r"SyntaxError:.*(?<!')\"(?!')$": {
            "description": "Unclosed string literal",
            "fix": "Ensure all strings have closing quotes"
        },
        r"SyntaxError:.*unexpected.*EOF": {
            "description": "Unexpected end of file",
            "fix": "Check for missing closing brackets/parentheses"
        },
        r"SyntaxError:.*invalid syntax": {
            "description": "Invalid Python syntax",
            "fix": "Check for typos, missing colons, incorrect indentation"
        },
        r"IndentationError": {
            "description": "Indentation error",
            "fix": "Use consistent indentation (4 spaces recommended)"
        },
    }
    
    RUNTIME_PATTERNS = {
        r"NameError:.*name '(\w+)' is not defined": {
            "description": "Undefined variable",
            "fix_template": "Define {var} before use or add import statement"
        },
        r"AttributeError:.*'NoneType'.*has no attribute": {
            "description": "Accessing attribute on None",
            "fix": "Add null check before accessing attribute"
        },
        r"TypeError:.*can't multiply sequence by non-int": {
            "description": "Invalid type operation",
            "fix": "Convert types appropriately before operation"
        },
        r"IndexError:.*index out of range": {
            "description": "List index out of range",
            "fix": "Check list length before accessing index"
        },
        r"KeyError:.*": {
            "description": "Dictionary key not found",
            "fix": "Use .get() method or check key existence first"
        },
        r"ImportError:.*No module named": {
            "description": "Missing module import",
            "fix": "Install required package or check import path"
        },
        r"FileNotFoundError:.*": {
            "description": "File not found",
            "fix": "Check file path, create file, or use proper path handling"
        },
        r"ZeroDivisionError": {
            "description": "Division by zero",
            "fix": "Add check to prevent division by zero"
        },
        r"PermissionError:.*": {
            "description": "Permission denied",
            "fix": "Check file permissions or run with appropriate access"
        },
        r"TimeoutError:.*": {
            "description": "Operation timed out",
            "fix": "Increase timeout, optimize operation, or add retry logic"
        },
    }
    
    @classmethod
    def get_pattern(cls, error_message: str) -> Optional[Dict]:
        """Get the fix pattern for an error message."""
        for pattern, info in {**cls.SYNTAX_PATTERNS, **cls.RUNTIME_PATTERNS}.items():
            if re.search(pattern, error_message, re.IGNORECASE):
                return info
        return None


class CodeHealer:
    """Self-healing code analyzer and fixer.
    
    Analyzes errors and attempts to fix them using pattern matching,
    LLM-based analysis, and recovery strategies.
    """
    
    def __init__(self, max_fix_attempts: int = 3):
        self.max_fix_attempts = max_fix_attempts
        self._fix_history: List[FixResult] = []
        self._recovery_strategies = self._initialize_strategies()
    
    def _initialize_strategies(self) -> List[RecoveryStrategy]:
        """Initialize default recovery strategies."""
        return [
            RecoveryStrategy(
                name="rollback",
                description="Rollback to previous working version",
                applies_to=["all"],
                priority=10
            ),
            RecoveryStrategy(
                name="retry_safe",
                description="Retry with safer parameters",
                applies_to=["TimeoutError", "ConnectionError", "PermissionError"],
                priority=20
            ),
            RecoveryStrategy(
                name="null_check",
                description="Add null/none check before operation",
                applies_to=["AttributeError", "TypeError"],
                priority=30
            ),
            RecoveryStrategy(
                name="type_convert",
                description="Convert types to match expected",
                applies_to=["TypeError"],
                priority=40
            ),
            RecoveryStrategy(
                name="define_missing",
                description="Define missing variables or imports",
                applies_to=["NameError", "ImportError"],
                priority=50
            ),
            RecoveryStrategy(
                name="bounds_check",
                description="Add bounds checking",
                applies_to=["IndexError", "KeyError"],
                priority=60
            ),
        ]
    
    def analyze_error(
        self,
        error: Exception,
        code: Optional[str] = None
    ) -> ErrorInfo:
        """Analyze an error and extract relevant information.
        
        Args:
            error: The exception that occurred
            code: Optional code that caused the error
            
        Returns:
            ErrorInfo with parsed error details
        """
        tb = traceback.format_exc()
        
        line_match = re.search(r'line (\d+)', tb)
        line_num = int(line_match.group(1)) if line_match else None
        
        file_match = re.search(r'File "([^"]+)"', tb)
        file_name = file_match.group(1) if file_match else None
        
        return ErrorInfo(
            error_type=type(error).__name__,
            error_message=str(error),
            traceback=tb,
            line_number=line_num,
            file_name=file_name,
            context={"code": code} if code else {}
        )
    
    async def attempt_fix(
        self,
        error_info: ErrorInfo,
        code: str,
        llm_provider: Optional[Any] = None
    ) -> FixResult:
        """Attempt to fix an error in code.
        
        Args:
            error_info: Parsed error information
            code: Original code with the error
            
        Returns:
            FixResult with fix details
        """
        result = FixResult(
            success=False,
            original_error=f"{error_info.error_type}: {error_info.error_message}",
            recovery_strategy="none"
        )
        
        pattern = ErrorPatternDatabase.get_pattern(error_info.error_message)
        
        if pattern:
            result.fix_applied = pattern.get("description", "Known pattern fix")
        
        if llm_provider:
            fixed_code = await self._fix_with_llm(code, error_info, llm_provider)
            if fixed_code:
                result.fixed_code = fixed_code
                result.success = True
                result.fix_applied = "LLM-based fix"
                result.recovery_strategy = "llm_repair"
        else:
            fixed_code = self._fix_with_patterns(code, error_info)
            if fixed_code:
                result.fixed_code = fixed_code
                result.success = True
                result.fix_applied = pattern.get("description", "Pattern fix") if pattern else "Simple fix"
                result.recovery_strategy = "pattern_match"
        
        if not result.success:
            result.recovery_strategy = self._get_best_strategy(error_info)
            result.fix_applied = f"Applied {result.recovery_strategy} strategy"
        
        result.attempts = 1
        self._fix_history.append(result)
        
        return result
    
    def _fix_with_patterns(self, code: str, error_info: ErrorInfo) -> Optional[str]:
        """Apply pattern-based fixes to code."""
        error_msg = error_info.error_message
        
        if "NameError" in error_info.error_type:
            match = re.search(r"name '(\w+)' is not defined", error_msg)
            if match:
                var_name = match.group(1)
                return code.replace(f"'{var_name}'", f"'{var_name}'") if var_name not in code else None
        
        if "IndentationError" in error_info.error_type:
            lines = code.split('\n')
            fixed_lines = []
            for line in lines:
                if line.strip() and not line.startswith(' ') and not line.startswith('\t'):
                    if line.strip() in ['def ', 'class ', 'if ', 'else:', 'elif ', 'for ', 'while ', 'try:', 'except:', 'finally:', 'with ']:
                        line = '    ' + line
                fixed_lines.append(line)
            return '\n'.join(fixed_lines)
        
        if "unexpected EOF" in error_msg.lower():
            lines = code.split('\n')
            while lines and not lines[-1].strip():
                lines.pop()
            if lines and not lines[-1].strip().endswith(':'):
                lines[-1] = lines[-1].rstrip().rstrip(',').rstrip(')')
            code = '\n'.join(lines) + '\n'
            return code
        
        return None
    
    async def _fix_with_llm(
        self,
        code: str,
        error_info: ErrorInfo,
        llm_provider: Any
    ) -> Optional[str]:
        """Use LLM to fix the error in code."""
        prompt = f"""Fix the following Python code that has an error.

Error: {error_info.error_type}: {error_info.error_message}
Line: {error_info.line_number}

Original Code:
```python
{code}
```

Requirements:
1. Fix ONLY the error, don't change working parts
2. Keep the same functionality
3. Add proper error handling
4. Return ONLY the fixed code, no explanations

Fix:"""

        try:
            response = await llm_provider.chat([
                {"role": "system", "content": "You are a code fixing assistant."},
                {"role": "user", "content": prompt}
            ])
            
            return self._extract_code(response)
        except Exception as e:
            logger.error(f"LLM fix failed: {e}")
            return None
    
    def _extract_code(self, response: str) -> Optional[str]:
        """Extract code from LLM response."""
        if '```python' in response:
            match = re.search(r'```python\n(.*?)```', response, re.DOTALL)
            if match:
                return match.group(1)
        elif '```' in response:
            match = re.search(r'```\n(.*?)```', response, re.DOTALL)
            if match:
                return match.group(1)
        
        lines = response.strip().split('\n')
        if lines and 'def ' in response:
            code_lines = []
            in_code = False
            for line in lines:
                if line.strip().startswith('```'):
                    in_code = not in_code
                    continue
                if in_code or 'def ' in line or 'class ' in line:
                    code_lines.append(line)
            if code_lines:
                return '\n'.join(code_lines)
        
        return None
    
    def _get_best_strategy(self, error_info: ErrorInfo) -> str:
        """Get the best recovery strategy for an error."""
        applicable = [
            s for s in self._recovery_strategies
            if s.can_handle(error_info.error_type)
        ]
        
        applicable.sort(key=lambda s: s.priority, reverse=True)
        
        return applicable[0].name if applicable else "fallback"
    
    def apply_safe_execution(
        self,
        func: Callable,
        *args,
        fallback_value: Any = None,
        **kwargs
    ) -> Tuple[Any, Optional[ErrorInfo]]:
        """Execute a function with automatic error recovery.
        
        Args:
            func: Function to execute
            fallback_value: Value to return on failure
            *args, **kwargs: Function arguments
            
        Returns:
            Tuple of (result, error_info) - error_info is None on success
        """
        try:
            result = func(*args, **kwargs)
            return result, None
        except Exception as e:
            error_info = self.analyze_error(e)
            logger.warning(f"Safe execution caught error: {error_info.error_type}")
            return fallback_value, error_info
    
    async def auto_repair(
        self,
        func: Callable,
        *args,
        llm_provider: Optional[Any] = None,
        **kwargs
    ) -> Tuple[Any, FixResult]:
        """Execute with auto-repair capability.
        
        Args:
            func: Function to execute
            llm_provider: Optional LLM for complex fixes
            
        Returns:
            Tuple of (result, fix_result)
        """
        error_info = None
        
        for attempt in range(self.max_fix_attempts):
            try:
                result = func(*args, **kwargs)
                return result, FixResult(
                    success=True,
                    original_error="",
                    recovery_strategy="none",
                    attempts=attempt + 1
                )
            except Exception as e:
                if error_info is None:
                    error_info = self.analyze_error(e)
                
                if attempt < self.max_fix_attempts - 1:
                    logger.info(f"Attempting fix (attempt {attempt + 1}/{self.max_fix_attempts})")
                    await asyncio.sleep(0.5 * (attempt + 1))
                else:
                    logger.error(f"Max fix attempts reached for {error_info.error_type}")
        
        return None, FixResult(
            success=False,
            original_error=str(error_info.error_message) if error_info else "Unknown",
            recovery_strategy="max_attempts",
            attempts=self.max_fix_attempts
        )
    
    def get_fix_history(self) -> List[Dict[str, Any]]:
        """Get history of fix attempts."""
        return [
            {
                "success": r.success,
                "original_error": r.original_error,
                "fix_applied": r.fix_applied,
                "recovery_strategy": r.recovery_strategy,
                "attempts": r.attempts,
            }
            for r in self._fix_history[-20:]
        ]


def extract_error_info(exception: Exception) -> Dict[str, Any]:
    """Extract structured error information from an exception.
    
    Args:
        exception: Exception to analyze
        
    Returns:
        Dictionary with error details
    """
    tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
    
    return {
        "type": type(exception).__name__,
        "message": str(exception),
        "traceback": "".join(tb),
        "timestamp": datetime.now().isoformat(),
    }


def suggest_fixes(error_message: str) -> List[str]:
    """Suggest potential fixes for an error message.
    
    Args:
        error_message: Error message to analyze
        
    Returns:
        List of suggested fixes
    """
    pattern = ErrorPatternDatabase.get_pattern(error_message)
    if not pattern:
        return []
    
    return [pattern.get("fix", "Review code for the error")] if pattern else []


async def heal_code(
    code: str,
    error: Exception,
    llm_provider: Optional[Any] = None,
    auto_register: bool = False
) -> Dict[str, Any]:
    """Convenience function to heal code with an error.
    
    Args:
        code: Code with error
        error: Exception that occurred
        llm_provider: Optional LLM for fixes
        auto_register: Whether to save healed code
        
    Returns:
        Dictionary with healing results
    """
    healer = CodeHealer()
    
    error_info = healer.analyze_error(error, code)
    result = await healer.attempt_fix(error_info, code, llm_provider)
    
    return {
        "success": result.success,
        "original_error": result.original_error,
        "fixed_code": result.fixed_code,
        "fix_applied": result.fix_applied,
        "recovery_strategy": result.recovery_strategy,
        "attempts": result.attempts,
        "error_type": error_info.error_type,
    }


__all__ = [
    "ErrorInfo",
    "FixResult",
    "RecoveryStrategy",
    "ErrorPatternDatabase",
    "CodeHealer",
    "extract_error_info",
    "suggest_fixes",
    "heal_code",
]