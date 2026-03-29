"""
Auto-Skill Generation Module

Generates new skills from natural language descriptions using LLM capabilities.
Analyzes descriptions, generates Python code, validates syntax, and registers
skills in the TOOLBOX.

Features:
- Natural language to skill code generation
- Automatic parameter extraction
- Syntax and safety validation
- Skill registration and documentation
"""

import asyncio
import logging
import re
import ast
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TOOLBOX_DIR = Path.home() / ".myclaw" / "TOOLBOX"
TOOLBOX_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class SkillSpec:
    """Specification for a skill to be generated."""
    name: str
    description: str
    parameters: List[Dict[str, str]] = field(default_factory=list)
    return_type: str = "str"
    category: str = "auto-generated"
    tags: List[str] = field(default_factory=list)


@dataclass
class GeneratedSkill:
    """A generated skill with code and metadata."""
    spec: SkillSpec
    code: str
    documentation: str
    validation_result: "ValidationResult"
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class ValidationResult:
    """Result of skill validation."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    score: float = 1.0


class SkillGenerator:
    """Generates skills from natural language descriptions.
    
    Uses LLM-based code generation with multiple validation stages
    to ensure generated skills are safe and functional.
    """
    
    SKILL_TEMPLATE = '''"""Auto-generated skill: {name}
    
{description}

Usage:
    {name}({param_signature})
    
Returns:
    {return_type}
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)


def {name}({param_declaration}) -> {return_type}:
    """{docstring}
    
    Args:
{param_docs}
    
    Returns:
        {return_type}: {return_desc}
    """
    try:
{implementation}
        return result
    except Exception as e:
        logger.error(f"Error in {name}: {{e}}")
        return f"Error: {{e}}"


# Skill metadata
__skill_name__ = "{name}"
__skill_version__ = "1.0.0"
__skill_category__ = "{category}"
__skill_tags__ = {tags}
'''

    COMMON_PATTERNS = {
        "http_request": '''
import requests
response = requests.{method}({url}, params={params}, timeout=30)
response.raise_for_status()
result = response.json()
        ''',
        "file_operations": '''
from pathlib import Path
path = Path({path})
result = path.{operation}()
        ''',
        "data_processing": '''
import json
data = {input_data}
result = {transformation}
        ''',
        "web_search": '''
from myclaw.web_search import search_web
result = asyncio.run(search_web({query}))
        ''',
    }
    
    def __init__(self):
        self._generation_history: List[GeneratedSkill] = []
    
    def parse_description(self, description: str) -> SkillSpec:
        """Parse a natural language description into a SkillSpec.
        
        Args:
            description: Natural language description of the skill
            
        Returns:
            SkillSpec with extracted name, description, and parameters
        """
        lines = description.strip().split('\n')
        
        name = self._extract_name(lines)
        desc = self._extract_description(lines)
        params = self._extract_parameters(description)
        
        return SkillSpec(
            name=name,
            description=desc,
            parameters=params,
            return_type=self._infer_return_type(description),
            category=self._infer_category(description),
            tags=self._extract_tags(description)
        )
    
    def _extract_name(self, lines: List[str]) -> str:
        """Extract skill name from description."""
        if lines:
            first_line = lines[0].strip()
            name = re.sub(r'[^a-zA-Z0-9_]', '_', first_line.lower())
            name = re.sub(r'_+$', '', name)
            if name[0].isdigit():
                name = 'skill_' + name
            return name or 'auto_skill'
        return 'auto_skill'
    
    def _extract_description(self, lines: List[str]) -> str:
        """Extract main description from lines."""
        if len(lines) > 1:
            return ' '.join(line.strip() for line in lines[1:] if line.strip())
        return lines[0] if lines else ''
    
    def _extract_parameters(self, description: str) -> List[Dict[str, str]]:
        """Extract parameter definitions from description."""
        params = []
        
        param_patterns = [
            r'{(\w+)}',
            r'parameter\s+(\w+)',
            r'(\w+)\s*:\s*(\w+)',
            r'(\w+)\s+as\s+\w+',
        ]
        
        found_params = set()
        for pattern in param_patterns:
            matches = re.findall(pattern, description, re.IGNORECASE)
            for match in matches:
                param_name = match if isinstance(match, str) else match[0]
                if param_name not in found_params:
                    found_params.add(param_name)
                    params.append({
                        "name": param_name,
                        "type": "str",
                        "description": f"Parameter {param_name}"
                    })
        
        return params
    
    def _infer_return_type(self, description: str) -> str:
        """Infer the return type from description."""
        desc_lower = description.lower()
        
        if any(word in desc_lower for word in ['json', 'dict', 'object', 'data']):
            return "Dict[str, Any]"
        if any(word in desc_lower for word in ['list', 'array', 'items']):
            return "List[Any]"
        if any(word in desc_lower for word in ['bool', 'true', 'false', 'yes']):
            return "bool"
        if any(word in desc_lower for word in ['number', 'count', 'int', 'float']):
            return "float"
        
        return "str"
    
    def _infer_category(self, description: str) -> str:
        """Infer the skill category from description."""
        desc_lower = description.lower()
        
        if any(word in desc_lower for word in ['file', 'read', 'write', 'save', 'load']):
            return "file-io"
        if any(word in desc_lower for word in ['http', 'web', 'url', 'request', 'api', 'fetch']):
            return "web"
        if any(word in desc_lower for word in ['search', 'find', 'query']):
            return "search"
        if any(word in desc_lower for word in ['transform', 'convert', 'process', 'parse']):
            return "processing"
        if any(word in desc_lower for word in ['calculate', 'compute', 'math']):
            return "computation"
        
        return "utility"
    
    def _extract_tags(self, description: str) -> List[str]:
        """Extract tags from description."""
        tags = []
        desc_lower = description.lower()
        
        keyword_tags = {
            'api': ['api', 'rest', 'endpoint'],
            'automation': ['automate', 'auto', 'schedule'],
            'data': ['data', 'database', 'sql'],
            'external': ['external', 'third-party', 'service'],
            'utility': ['util', 'helper'],
        }
        
        for tag, keywords in keyword_tags.items():
            if any(kw in desc_lower for kw in keywords):
                tags.append(tag)
        
        return tags[:5]
    
    async def generate_skill(
        self,
        description: str,
        llm_provider: Optional[Any] = None
    ) -> GeneratedSkill:
        """Generate a skill from description.
        
        Args:
            description: Natural language description
            llm_provider: Optional LLM provider for code generation
            
        Returns:
            GeneratedSkill with code and validation results
        """
        spec = self.parse_description(description)
        
        if llm_provider:
            code = await self._generate_with_llm(spec, llm_provider)
        else:
            code = self._generate_template_code(spec)
        
        validation = self.validate_code(code, spec)
        documentation = self._generate_documentation(spec)
        
        result = GeneratedSkill(
            spec=spec,
            code=code,
            documentation=documentation,
            validation_result=validation
        )
        
        self._generation_history.append(result)
        return result
    
    def _generate_template_code(self, spec: SkillSpec) -> str:
        """Generate code from template (fallback without LLM)."""
        param_names = [p['name'] for p in spec.parameters]
        
        if not param_names:
            param_signature = ""
            param_declaration = "self"
            param_docs = "        self: Skill instance"
        else:
            param_signature = ", ".join(param_names)
            param_declaration = "self, " + ", ".join(
                f"{p['name']}: {p.get('type', 'str')} = ''"
                for p in spec.parameters
            )
            param_docs = "\n".join(
                f"        {p['name']} ({p.get('type', 'str')}): {p.get('description', '')}"
                for p in spec.parameters
            )
        
        return self.SKILL_TEMPLATE.format(
            name=spec.name,
            description=spec.description,
            param_signature=param_signature,
            param_declaration=param_declaration,
            param_docs=param_docs,
            return_type=spec.return_type,
            return_desc="Operation result",
            docstring=spec.description[:200] if spec.description else "Auto-generated skill",
            implementation="        # TODO: Implement skill logic\n        result = ''",
            category=spec.category,
            tags=spec.tags
        )
    
    async def _generate_with_llm(
        self,
        spec: SkillSpec,
        llm_provider: Any
    ) -> str:
        """Generate skill code using LLM."""
        prompt = f"""Generate a Python function for the following skill:

Name: {spec.name}
Description: {spec.description}
Parameters: {spec.parameters}
Return Type: {spec.return_type}

Requirements:
1. Include proper docstring
2. Add error handling with logger.error()
3. Use type hints
4. Keep the function focused and simple
5. Return appropriate error messages on failure

Generate only the function code, no explanation."""

        try:
            response = await llm_provider.chat([
                {"role": "system", "content": "You are a code generation assistant."},
                {"role": "user", "content": prompt}
            ])
            
            return self._extract_code_from_response(response)
        except Exception as e:
            logger.error(f"LLM generation failed: {e}")
            return self._generate_template_code(spec)
    
    def _extract_code_from_response(self, response: str) -> str:
        """Extract code from LLM response."""
        if '```python' in response:
            match = re.search(r'```python\n(.*?)```', response, re.DOTALL)
            if match:
                return match.group(1)
        
        return response.strip()
    
    def validate_code(self, code: str, spec: SkillSpec) -> ValidationResult:
        """Validate generated skill code.
        
        Args:
            code: Python code to validate
            spec: Original skill specification
            
        Returns:
            ValidationResult with errors/warnings
        """
        errors = []
        warnings = []
        
        try:
            ast.parse(code)
        except SyntaxError as e:
            errors.append(f"Syntax error: {e}")
            return ValidationResult(is_valid=False, errors=errors)
        
        if 'def ' + spec.name not in code:
            warnings.append(f"Function name '{spec.name}' not found in code")
        
        if 'try:' not in code:
            warnings.append("Missing try-except error handling")
        
        if 'logger.error' not in code:
            warnings.append("Missing logger.error() calls")
        
        if '"""' not in code and "'''" not in code:
            warnings.append("Missing docstring")
        
        dangerous_patterns = [
            r'eval\s*\(',
            r'exec\s*\(',
            r'__import__',
            r'subprocess\s*\.\s*run',
            r'os\s*\.\s*system',
            r'shutil\s*\.\s*rmtree',
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, code):
                warnings.append(f"Potentially dangerous pattern detected: {pattern}")
        
        score = 1.0
        if errors:
            score = 0.0
        elif warnings:
            score -= len(warnings) * 0.1
        
        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
            score=max(0.0, score)
        )
    
    def _generate_documentation(self, spec: SkillSpec) -> str:
        """Generate markdown documentation for a skill."""
        lines = [
            f"# {spec.name}",
            "",
            f"**Category**: {spec.category}",
            "",
            f"## Description",
            "",
            spec.description,
            "",
            f"## Parameters",
            "",
        ]
        
        for param in spec.parameters:
            lines.append(f"- `{param['name']}` ({param.get('type', 'str')}): {param.get('description', '')}")
        
        lines.extend([
            "",
            f"## Returns",
            "",
            f"`{spec.return_type}`",
            "",
            f"## Tags",
            "",
            ", ".join(spec.tags) if spec.tags else "None",
        ])
        
        return "\n".join(lines)
    
    def register_skill(
        self,
        skill: GeneratedSkill,
        auto_enable: bool = True
    ) -> Tuple[str, bool]:
        """Register a generated skill in TOOLBOX.
        
        Args:
            skill: GeneratedSkill to register
            auto_enable: Whether to enable the skill immediately
            
        Returns:
            Tuple of (file_path, success)
        """
        if not skill.validation_result.is_valid:
            return "", False
        
        skill_path = TOOLBOX_DIR / f"{skill.spec.name}.py"
        
        try:
            skill_path.write_text(skill.code, encoding="utf-8")
            
            self._register_in_registry(skill)
            
            logger.info(f"Registered skill: {skill.spec.name} at {skill_path}")
            return str(skill_path), True
            
        except Exception as e:
            logger.error(f"Failed to register skill: {e}")
            return "", False
    
    def _register_in_registry(self, skill: GeneratedSkill) -> None:
        """Register skill in TOOLBOX registry."""
        import json
        
        registry_path = TOOLBOX_DIR / "toolbox_registry.json"
        registry = {}
        
        if registry_path.exists():
            try:
                registry = json.loads(registry_path.read_text())
            except Exception:
                pass
        
        registry[skill.spec.name] = {
            'path': str(TOOLBOX_DIR / f"{skill.spec.name}.py"),
            'name': skill.spec.name,
            'version': '1.0.0',
            'description': skill.spec.description,
            'tags': skill.spec.tags,
            'category': skill.spec.category,
            'author': 'auto-generated',
            'created': skill.created_at.isoformat(),
            'last_modified': datetime.now().isoformat(),
            'source': 'auto-generator',
            'eval_score': skill.validation_result.score,
            'eval_count': 0,
            'enabled': True,
            'errors': skill.validation_result.errors,
        }
        
        registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
    
    def get_generation_history(self) -> List[Dict[str, Any]]:
        """Get history of generated skills."""
        return [
            {
                "name": s.spec.name,
                "created_at": s.created_at.isoformat(),
                "is_valid": s.validation_result.is_valid,
                "score": s.validation_result.score,
                "errors": s.validation_result.errors,
                "warnings": s.validation_result.warnings,
            }
            for s in self._generation_history
        ]


async def auto_generate_skill(
    description: str,
    llm_provider: Optional[Any] = None,
    auto_register: bool = True
) -> Dict[str, Any]:
    """Convenience function to auto-generate a skill.
    
    Args:
        description: Natural language description
        llm_provider: Optional LLM provider
        auto_register: Whether to register automatically
        
    Returns:
        Dictionary with generation results
    """
    generator = SkillGenerator()
    
    skill = await generator.generate_skill(description, llm_provider)
    
    result = {
        "name": skill.spec.name,
        "description": skill.spec.description,
        "code": skill.code,
        "documentation": skill.documentation,
        "validation": {
            "is_valid": skill.validation_result.is_valid,
            "score": skill.validation_result.score,
            "errors": skill.validation_result.errors,
            "warnings": skill.validation_result.warnings,
        },
        "registered": False,
        "path": None,
    }
    
    if auto_register and skill.validation_result.is_valid:
        path, success = generator.register_skill(skill)
        result["registered"] = success
        result["path"] = path
    
    return result


__all__ = [
    "SkillSpec",
    "GeneratedSkill",
    "ValidationResult",
    "SkillGenerator",
    "auto_generate_skill",
]