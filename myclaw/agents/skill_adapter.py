"""Skill Adapter - Analyzes and converts external skills from agentskills.io standard to ZenSynora format."""

import json
import logging
import re
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)

TOOLBOX_DIR = Path.home() / ".myclaw" / "TOOLBOX"
TOOLBOX_REG = TOOLBOX_DIR / "toolbox_registry.json"


class SkillAdapter:
    """Adapts external skill standards to ZenSynora TOOLBOX format.
    
    Supports parsing from agentskills.io JSON format and converting to
    ZenSynora's skill registry format.
    """

    def __init__(self):
        self.toolbox_dir = TOOLBOX_DIR
        self.toolbox_dir.mkdir(parents=True, exist_ok=True)
        self.registry_path = TOOLBOX_REG

    def parse_external_skill(self, skill_data: Any, source: str = "json") -> Optional[Dict]:
        """Parse skill from external format to internal representation.
        
        Args:
            skill_data: Skill data as dict, JSON string, or file path
            source: Format type ('json', 'yaml', 'agentskills')
        
        Returns:
            Parsed skill dict or None on error
        """
        try:
            if isinstance(skill_data, str):
                if skill_data.startswith(('{', '[')) or skill_data.startswith('---'):
                    skill_dict = json.loads(skill_data) if source != 'yaml' else self._parse_yaml(skill_data)
                else:
                    path = Path(skill_data)
                    if path.exists():
                        content = path.read_text(encoding="utf-8")
                        skill_dict = json.loads(content) if path.suffix == '.json' else self._parse_yaml(content)
                    else:
                        logger.error(f"Skill file not found: {skill_data}")
                        return None
            elif isinstance(skill_data, dict):
                skill_dict = skill_data
            else:
                logger.error(f"Unsupported skill data type: {type(skill_data)}")
                return None

            normalized = self._normalize_skill_schema(skill_dict)
            logger.info(f"Parsed external skill: {normalized.get('name', 'unknown')}")
            return normalized

        except Exception as e:
            logger.error(f"Error parsing external skill: {e}")
            return None

    def _normalize_skill_schema(self, skill_dict: Dict) -> Dict:
        """Normalize various skill schemas to internal format."""
        name = skill_dict.get('name', skill_dict.get('skill_name', 'unnamed'))
        return {
            'name': self._sanitize_name(name),
            'description': skill_dict.get('description', skill_dict.get('desc', '')),
            'parameters': skill_dict.get('parameters', []),
            'function': skill_dict.get('function', skill_dict.get('code', '')),
            'tags': skill_dict.get('tags', skill_dict.get('categories', [])),
            'version': skill_dict.get('version', '1.0.0'),
            'author': skill_dict.get('author', 'external'),
            'source': skill_dict.get('source', 'agentskills.io'),
            'created': datetime.now().isoformat(),
        }

    def _sanitize_name(self, name: str) -> str:
        """Convert name to valid Python identifier."""
        sanitized = re.sub(r'[^a-zA-Z0-9_]', '_', name.lower())
        sanitized = sanitized.strip('_')
        if sanitized[0].isdigit():
            sanitized = 'skill_' + sanitized
        return sanitized or 'unnamed_skill'

    def _parse_yaml(self, content: str) -> Dict:
        """Simple YAML parser for basic skill files."""
        result = {}
        in_code_block = False
        code_lines = []
        
        for line in content.split('\n'):
            if line.strip().startswith('name:'):
                result['name'] = line.split(':', 1)[1].strip().strip('"').strip("'")
            elif line.strip().startswith('description:'):
                result['description'] = line.split(':', 1)[1].strip().strip('"').strip("'")
            elif line.strip().startswith('tags:'):
                tags = []
                for tag_line in content.split('\n'):
                    if tag_line.strip().startswith('- '):
                        tags.append(tag_line.strip()[2:])
                result['tags'] = tags
            elif line.strip().startswith('version:'):
                result['version'] = line.split(':', 1)[1].strip().strip('"').strip("'")
            elif '```' in line:
                if not in_code_block:
                    in_code_block = True
                else:
                    in_code_block = False
                    result['function'] = '\n'.join(code_lines)
                    code_lines = []
            elif in_code_block:
                code_lines.append(line)
        
        return result

    def convert_to_zensynora(self, external_skill: Dict) -> str:
        """Convert external skill to ZenSynora Python function.
        
        Args:
            external_skill: Parsed skill dict from parse_external_skill()
        
        Returns:
            Path to the created skill file
        """
        name = external_skill['name']
        description = external_skill.get('description', '')
        params = external_skill.get('parameters', [])
        function_code = external_skill.get('function', '')
        
        param_str = ', '.join([
            f"{p.get('name', f'param{i}')}: {p.get('type', 'str')} = ''"
            for i, p in enumerate(params)
        ])
        
        imports = []
        if 'requests' in function_code.lower():
            imports.append('import requests')
        if 'path' in function_code.lower():
            imports.append('from pathlib import Path')
        if 'json' in function_code.lower():
            imports.append('import json')
        
        import_block = '\n'.join(imports) + '\n\n' if imports else ''
        
        code = f'''"""Converted skill: {name} from {external_skill.get('source', 'external')}."""

import logging
{import_block}logger = logging.getLogger(__name__)


def {name}({param_str}) -> str:
    """{description or f"Skill: {name}"}
    
    Parameters:
''' + '\n'.join([f'        {p.get("name", f"param{i}")}: {p.get("type", "str")} - {p.get("description", "")}' 
                     for i, p in enumerate(params)]) + '''
    
    Returns:
        Result string from skill execution.
    '''
        if function_code and not function_code.startswith('#'):
            code += f'''
    try:
{self._indent_code(function_code)}
        return str(result)
    except Exception as e:
        logger.error(f"Skill {name} error: {{e}}")
        return f"Error: {{e}}"
'''
        else:
            code += '''    # Implementation not available - skill needs manual review
    return "This skill requires implementation."
'''

        skill_path = self.toolbox_dir / f"{name}.py"
        skill_path.write_text(code, encoding="utf-8")
        
        self._register_in_toolbox(external_skill, str(skill_path))
        
        logger.info(f"Converted skill saved to: {skill_path}")
        return str(skill_path)

    def _indent_code(self, code: str, indent: int = 8) -> str:
        """Indent code block by specified spaces."""
        indent_str = ' ' * indent
        return '\n'.join(indent_str + line if line.strip() else '' for line in code.split('\n'))

    def _register_in_toolbox(self, skill_info: Dict, skill_path: str) -> None:
        """Register converted skill in TOOLBOX registry."""
        registry = {}
        if self.registry_path.exists():
            try:
                registry = json.loads(self.registry_path.read_text())
            except Exception as e:
                logger.error(f"Error reading registry: {e}")

        name = skill_info['name']
        registry[name] = {
            'path': skill_path,
            'name': name,
            'version': skill_info.get('version', '1.0.0'),
            'description': skill_info.get('description', ''),
            'tags': skill_info.get('tags', []),
            'author': skill_info.get('author', 'external'),
            'created': skill_info.get('created', datetime.now().isoformat()),
            'last_modified': datetime.now().isoformat(),
            'source': skill_info.get('source', 'agentskills.io'),
            'eval_score': None,
            'eval_count': 0,
            'enabled': True,
            'errors': [],
        }

        self.registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    def discover_external(self, url: Optional[str] = None) -> List[Dict]:
        """Discover available external skills from registry or URL.
        
        Args:
            url: Optional URL to fetch skill list from
        
        Returns:
            List of available skill metadata dicts
        """
        discovered = []
        
        if url:
            try:
                import requests
                response = requests.get(url, timeout=10)
                if response.status_code == 200:
                    data = response.json()
                    if isinstance(data, list):
                        for item in data:
                            parsed = self.parse_external_skill(item)
                            if parsed:
                                discovered.append(parsed)
            except Exception as e:
                logger.error(f"Error fetching from URL: {e}")
        
        if self.registry_path.exists():
            try:
                registry = json.loads(self.registry_path.read_text())
                for name, info in registry.items():
                    if info.get('source') not in ('local', 'zen-synora'):
                        discovered.append({
                            'name': name,
                            'version': info.get('version', '1.0.0'),
                            'source': info.get('source', 'unknown'),
                            'tags': info.get('tags', []),
                        })
            except Exception as e:
                logger.error(f"Error reading local registry: {e}")
        
        return discovered

    def generate_wrapper(self, skill_name: str, func_code: str) -> str:
        """Generate a wrapper for an external skill function.
        
        Args:
            skill_name: Name of the skill
            func_code: Function source code
        
        Returns:
            Wrapped function code
        """
        return f'''"""Wrapper for external skill: {skill_name}."""

import logging
logger = logging.getLogger(__name__)


def {skill_name}_wrapper(*args, **kwargs):
    """Auto-generated wrapper for {skill_name}."""
    try:
        # TODO: Implement wrapper logic
        return "Wrapper for {skill_name} not yet implemented"
    except Exception as e:
        logger.error(f"Wrapper error for {skill_name}: {{e}}")
        return f"Error: {{e}}"
'''

    def list_compatible(self) -> List[Dict]:
        """List all compatible external skills in the TOOLBOX."""
        if not self.registry_path.exists():
            return []
        
        try:
            registry = json.loads(self.registry_path.read_text())
            return [
                {'name': name, 'source': info.get('source', 'unknown'), 'version': info.get('version')}
                for name, info in registry.items()
                if info.get('source') not in (None, 'local', 'zen-synora')
            ]
        except Exception as e:
            logger.error(f"Error listing compatible skills: {e}")
            return []


def analyze_external_skill(skill_url: str, style: str = "json") -> str:
    """Analyze an external skill and return compatibility report.
    
    Args:
        skill_url: URL or file path to the skill
        style: Format style ('json', 'yaml', 'agentskills')
    
    Returns:
        Formatted analysis report
    """
    adapter = SkillAdapter()
    
    try:
        if skill_url.startswith(('http://', 'https://')):
            import requests
            response = requests.get(skill_url, timeout=10)
            if response.status_code == 200:
                skill_data = response.json()
            else:
                return f"Error: Failed to fetch skill (status {response.status_code})"
        else:
            path = Path(skill_url)
            if path.exists():
                skill_data = json.loads(path.read_text())
            else:
                return f"Error: File not found: {skill_url}"
        
        parsed = adapter.parse_external_skill(skill_data)
        if not parsed:
            return "Error: Failed to parse skill data"
        
        lines = [
            f"📋 External Skill Analysis",
            f"",
            f"Name: {parsed['name']}",
            f"Source: {parsed.get('source', 'unknown')}",
            f"Version: {parsed.get('version', '1.0.0')}",
            f"Description: {parsed.get('description', 'N/A')}",
            f"Tags: {', '.join(parsed.get('tags', [])) or 'None'}",
            f"Parameters: {len(parsed.get('parameters', []))}",
        ]
        
        for i, p in enumerate(parsed.get('parameters', [])):
            lines.append(f"  - {p.get('name', f'param{i}')}: {p.get('type', 'str')}")
        
        has_function = bool(parsed.get('function'))
        lines.append(f"Has Implementation: {'Yes' if has_function else 'No'}")
        lines.append(f"Convertible: {'Yes' if has_function else 'Needs manual review'}")
        
        return "\n".join(lines)
    
    except Exception as e:
        logger.error(f"Error analyzing skill: {e}")
        return f"Error analyzing skill: {e}"


def convert_skill(skill_source: str, target_format: str = "zensynora") -> str:
    """Convert an external skill to ZenSynora format.
    
    Args:
        skill_source: URL, file path, or JSON string of the skill
        target_format: Target format (default: 'zensynora')
    
    Returns:
        Conversion result with file path
    """
    adapter = SkillAdapter()
    
    try:
        skill_data = None
        if skill_source.startswith(('http://', 'https://')):
            import requests
            response = requests.get(skill_source, timeout=10)
            if response.status_code == 200:
                skill_data = response.json()
        else:
            try:
                skill_data = json.loads(skill_source)
            except json.JSONDecodeError:
                path = Path(skill_source)
                if path.exists():
                    skill_data = json.loads(path.read_text())
        
        if not skill_data:
            return "Error: Could not parse skill source"
        
        parsed = adapter.parse_external_skill(skill_data)
        if not parsed:
            return "Error: Failed to parse skill"
        
        file_path = adapter.convert_to_zensynora(parsed)
        
        return f"✅ Skill converted successfully!\nFile: {file_path}\nName: {parsed['name']}"
    
    except Exception as e:
        logger.error(f"Error converting skill: {e}")
        return f"Error converting skill: {e}"


def list_compatible_skills() -> str:
    """List all skills from external sources that are compatible with ZenSynora.
    
    Returns:
        Formatted list of compatible skills
    """
    adapter = SkillAdapter()
    skills = adapter.list_compatible()
    
    if not skills:
        return "No external skills found. Use analyze_external_skill() to add skills."
    
    lines = ["📦 Compatible External Skills:", ""]
    for skill in skills:
        lines.append(f"  • {skill['name']} (v{skill.get('version', '1.0.0')}) - {skill.get('source', 'unknown')}")
    
    return "\n".join(lines)


def register_external_skill(skill_path: str) -> str:
    """Register an externally sourced skill file in the TOOLBOX.
    
    Args:
        skill_path: Path to the skill Python file
    
    Returns:
        Registration result
    """
    adapter = SkillAdapter()
    
    path = Path(skill_path)
    if not path.exists():
        return f"Error: File not found: {skill_path}"
    
    try:
        code = path.read_text(encoding="utf-8")
        compile(code, path.name, "exec")
        
        registry = {}
        if adapter.registry_path.exists():
            try:
                registry = json.loads(adapter.registry_path.read_text())
            except Exception:
                pass
        
        name = path.stem
        registry[name] = {
            'path': str(path),
            'name': name,
            'version': '1.0.0',
            'description': 'Imported from external source',
            'tags': ['imported', 'external'],
            'author': 'external',
            'created': datetime.now().isoformat(),
            'last_modified': datetime.now().isoformat(),
            'source': 'external',
            'eval_score': None,
            'eval_count': 0,
            'enabled': True,
            'errors': [],
        }
        
        adapter.registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        
        return f"✅ Skill '{name}' registered from {skill_path}"
    
    except SyntaxError as e:
        return f"Error: Invalid Python syntax in skill file: {e}"
    except Exception as e:
        logger.error(f"Error registering skill: {e}")
        return f"Error registering skill: {e}"