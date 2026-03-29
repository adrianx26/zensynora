"""
ZenHub - Local Skill Registry for ZenSynora

Provides a central registry for discovering, installing, and publishing skills.
Inspired by ClawHub, supports local-first skill management.

Directory Structure:
    ~/.myclaw/hub/           - ZenHub registry root
    ~/.myclaw/hub/skills/     - Published skills
    ~/.myclaw/hub/index.json - Skill index/metadata
    ~/.myclaw/skills/         - External skill directory (auto-discovered)
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

HUB_DIR = Path.home() / ".myclaw" / "hub"
HUB_SKILLS_DIR = HUB_DIR / "skills"
HUB_INDEX = HUB_DIR / "index.json"
EXTERNAL_SKILLS_DIR = Path.home() / ".myclaw" / "skills"


def _ensure_hub_dirs():
    """Create hub directory structure if it doesn't exist."""
    HUB_DIR.mkdir(parents=True, exist_ok=True)
    HUB_SKILLS_DIR.mkdir(parents=True, exist_ok=True)


def _load_index() -> Dict:
    """Load the hub index or create empty one."""
    _ensure_hub_dirs()
    if HUB_INDEX.exists():
        try:
            return json.loads(HUB_INDEX.read_text())
        except Exception:
            pass
    return {"skills": {}, "last_updated": None}


def _save_index(index: Dict):
    """Save the hub index."""
    index["last_updated"] = datetime.now().isoformat()
    HUB_INDEX.write_text(json.dumps(index, indent=2))


def hub_search(query: str, limit: int = 10) -> str:
    """Search the local ZenHub registry for skills.
    
    Searches skill names, descriptions, and tags.
    
    query: Search query string
    limit: Maximum results to return (default: 10)
    
    Returns:
        Formatted list of matching skills.
    """
    _ensure_hub_dirs()
    
    index = _load_index()
    skills = index.get("skills", {})
    
    if not skills:
        return "ZenHub is empty. No skills published yet. Use hub_publish() to publish a skill."
    
    query_lower = query.lower()
    results = []
    
    for name, info in skills.items():
        # Search in name, description, tags
        searchable = " ".join([
            name,
            info.get("description", ""),
            " ".join(info.get("tags", []))
        ]).lower()
        
        if query_lower in searchable:
            results.append((name, info))
    
    if not results:
        return f"No skills found matching '{query}'. Try hub_list() to see all available skills."
    
    lines = [f"🔍 ZenHub Search Results for '{query}':", ""]
    
    for name, info in sorted(results, key=lambda x: x[1].get("downloads", 0), reverse=True)[:limit]:
        lines.extend([
            f"📦 **{name}** (v{info.get('version', '1.0.0')})",
            f"   {info.get('description', 'No description')[:100]}",
            f"   Author: {info.get('author', 'unknown')} | Downloads: {info.get('downloads', 0)}",
            f"   Tags: {', '.join(info.get('tags', [])) or 'none'}",
            ""
        ])
    
    return "\n".join(lines)


def hub_list() -> str:
    """List all skills available in ZenHub.
    
    Returns:
        Formatted list of all published skills.
    """
    _ensure_hub_dirs()
    
    index = _load_index()
    skills = index.get("skills", {})
    
    if not skills:
        return "ZenHub is empty. No skills published yet."
    
    lines = [f"📚 ZenHub Registry ({len(skills)} skills):", ""]
    
    for name, info in sorted(skills.items()):
        lines.extend([
            f"📦 **{name}** v{info.get('version', '1.0.0')}",
            f"   {info.get('description', 'No description')[:80]}...",
            f"   Tags: {', '.join(info.get('tags', [])) or 'none'}",
            ""
        ])
    
    return "\n".join(lines)


def hub_publish(skill_name: str, description: str = "", tags: str = "", 
                from_toolbox: bool = True) -> str:
    """Publish a skill from TOOLBOX to ZenHub.
    
    Makes a skill available for others to install from ZenHub.
    
    skill_name: Name of the skill to publish
    description: Skill description (uses existing if empty)
    tags: Comma-separated tags
    from_toolbox: If True, copy from TOOLBOX (default). If False, look in external skills dir.
    
    Returns:
        Success or error message.
    """
    _ensure_hub_dirs()
    
    # Find the skill source
    if from_toolbox:
        from ..tools import TOOLBOX_REG, TOOLBOX_DIR
        if not TOOLBOX_REG.exists():
            return "TOOLBOX registry not found."
        
        try:
            registry = json.loads(TOOLBOX_REG.read_text())
        except Exception as e:
            return f"Error reading TOOLBOX registry: {e}"
        
        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."
        
        info = registry[skill_name]
        source_path = Path(info.get("path", ""))
        if not source_path.exists():
            return f"Skill file not found at: {source_path}"
        
    else:
        source_path = EXTERNAL_SKILLS_DIR / f"{skill_name}.py"
        if not source_path.exists():
            return f"Skill '{skill_name}' not found in {EXTERNAL_SKILLS_DIR}."
        
        info = {
            "name": skill_name,
            "version": "1.0.0",
            "description": description,
            "author": "user"
        }
    
    # Copy to hub
    dest_path = HUB_SKILLS_DIR / f"{skill_name}.py"
    try:
        import shutil
        shutil.copy2(source_path, dest_path)
    except Exception as e:
        return f"Error copying skill: {e}"
    
    # Update index
    index = _load_index()
    index["skills"][skill_name] = {
        "name": skill_name,
        "version": info.get("version", "1.0.0"),
        "description": description or info.get("description", ""),
        "tags": [t.strip() for t in tags.split(",") if t.strip()] if tags else info.get("tags", []),
        "author": info.get("author", "unknown"),
        "published": datetime.now().isoformat(),
        "downloads": 0,
        "path": str(dest_path)
    }
    _save_index(index)
    
    logger.info(f"Published skill to ZenHub: {skill_name}")
    return f"✅ Skill '{skill_name}' published to ZenHub."


def hub_install(skill_name: str, user_id: str = "default") -> str:
    """Install a skill from ZenHub into TOOLBOX.
    
    Copies the skill from ZenHub to TOOLBOX and registers it.
    
    skill_name: Name of the skill to install
    user_id: User ID (for notification purposes)
    
    Returns:
        Success or error message.
    """
    _ensure_hub_dirs()
    
    index = _load_index()
    skills = index.get("skills", {})
    
    if skill_name not in skills:
        return f"Skill '{skill_name}' not found in ZenHub. Use hub_list() to see available skills."
    
    info = skills[skill_name]
    source_path = Path(info.get("path", ""))
    
    if not source_path.exists():
        return f"Skill file not found in ZenHub: {source_path}"
    
    # Copy to TOOLBOX
    from ..tools import TOOLBOX_DIR, TOOLBOX_REG, TOOLS, TOOL_SCHEMAS
    import importlib.util
    import inspect
    
    TOOLBOX_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = TOOLBOX_DIR / f"{skill_name}.py"
    
    try:
        import shutil
        shutil.copy2(source_path, dest_path)
    except Exception as e:
        return f"Error copying skill: {e}"
    
    # Load the skill
    try:
        spec = importlib.util.spec_from_file_location(skill_name, dest_path)
        if spec is None or spec.loader is None:
            return f"Could not load skill '{skill_name}'."
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        func = getattr(mod, skill_name)
        TOOLS[skill_name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {skill_name}"}
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas_from_tools())
    except Exception as e:
        return f"Error loading skill: {e}"
    
    # Update TOOLBOX registry
    registry = {}
    if TOOLBOX_REG.exists():
        try:
            registry = json.loads(TOOLBOX_REG.read_text())
        except Exception:
            pass
    
    registry[skill_name] = {
        "path": str(dest_path),
        "name": skill_name,
        "version": info.get("version", "1.0.0"),
        "description": info.get("description", ""),
        "tags": info.get("tags", []),
        "author": info.get("author", "unknown"),
        "created": datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
        "eval_score": None,
        "eval_count": 0,
        "enabled": True,
        "errors": []
    }
    TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
    
    # Update download count
    index["skills"][skill_name]["downloads"] = index["skills"][skill_name].get("downloads", 0) + 1
    _save_index(index)
    
    logger.info(f"Installed skill from ZenHub: {skill_name}")
    return f"✅ Skill '{skill_name}' installed from ZenHub (v{info.get('version', '1.0.0')})"


def hub_remove(skill_name: str) -> str:
    """Remove a skill from ZenHub (unpublish).
    
    skill_name: Name of the skill to remove
    
    Returns:
        Success or error message.
    """
    _ensure_hub_dirs()
    
    index = _load_index()
    skills = index.get("skills", {})
    
    if skill_name not in skills:
        return f"Skill '{skill_name}' not found in ZenHub."
    
    info = skills[skill_name]
    hub_path = Path(info.get("path", ""))
    
    # Remove from index
    del index["skills"][skill_name]
    _save_index(index)
    
    # Remove file
    if hub_path.exists():
        try:
            hub_path.unlink()
        except Exception as e:
            logger.warning(f"Could not remove skill file: {e}")
    
    logger.info(f"Removed skill from ZenHub: {skill_name}")
    return f"✅ Skill '{skill_name}' removed from ZenHub."


def discover_external_skills() -> str:
    """Auto-discover skills in external directory (~/.myclaw/skills/).
    
    Scans the external skills directory and returns a list of
    skills that can be imported into TOOLBOX or published to ZenHub.
    
    Returns:
        Formatted list of discovered skills.
    """
    if not EXTERNAL_SKILLS_DIR.exists():
        return f"External skills directory not found: {EXTERNAL_SKILLS_DIR}"
    
    discovered = []
    for py_file in EXTERNAL_SKILLS_DIR.glob("*.py"):
        skill_name = py_file.stem
        try:
            code = py_file.read_text()
            
            # Extract docstring
            docstring = ""
            if '"""' in code:
                docstring = code.split('"""')[1].split('"""')[0].strip()[:100]
            elif "'''" in code:
                docstring = code.split("'''")[1].split("'''")[0].strip()[:100]
            
            discovered.append({
                "name": skill_name,
                "path": str(py_file),
                "docstring": docstring,
                "size": py_file.stat().st_size
            })
        except Exception as e:
            logger.warning(f"Error reading {py_file}: {e}")
    
    if not discovered:
        return f"No skills found in {EXTERNAL_SKILLS_DIR}"
    
    lines = [f"🔍 Discovered {len(discovered)} skills in external directory:", ""]
    
    for skill in discovered:
        lines.extend([
            f"📦 **{skill['name']}**",
            f"   Path: {skill['path']}",
            f"   Size: {skill['size']} bytes",
            f"   Doc: {skill['docstring'] or 'No docstring'}...",
            ""
        ])
    
    lines.append("Use hub_install_from_external(skill_name) to import, or hub_publish() to publish to ZenHub.")
    
    return "\n".join(lines)


def hub_install_from_external(skill_name: str, user_id: str = "default") -> str:
    """Install a skill from the external directory into TOOLBOX.
    
    skill_name: Name of the skill file (without .py)
    user_id: User ID (for notification purposes)
    
    Returns:
        Success or error message.
    """
    source_path = EXTERNAL_SKILLS_DIR / f"{skill_name}.py"
    
    if not source_path.exists():
        return f"Skill '{skill_name}' not found in {EXTERNAL_SKILLS_DIR}"
    
    # Install to TOOLBOX (reuse hub_install logic)
    from ..tools import TOOLBOX_DIR, TOOLBOX_REG, TOOLS, TOOL_SCHEMAS
    import importlib.util
    import inspect
    
    TOOLBOX_DIR.mkdir(parents=True, exist_ok=True)
    dest_path = TOOLBOX_DIR / f"{skill_name}.py"
    
    try:
        import shutil
        shutil.copy2(source_path, dest_path)
    except Exception as e:
        return f"Error copying skill: {e}"
    
    # Load the skill
    try:
        spec = importlib.util.spec_from_file_location(skill_name, dest_path)
        if spec is None or spec.loader is None:
            return f"Could not load skill '{skill_name}'."
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        func = getattr(mod, skill_name)
        TOOLS[skill_name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {skill_name}"}
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas_from_tools())
    except Exception as e:
        return f"Error loading skill: {e}"
    
    # Update TOOLBOX registry
    registry = {}
    if TOOLBOX_REG.exists():
        try:
            registry = json.loads(TOOLBOX_REG.read_text())
        except Exception:
            pass
    
    registry[skill_name] = {
        "path": str(dest_path),
        "name": skill_name,
        "version": "1.0.0",
        "description": "Installed from external skills directory",
        "tags": ["external"],
        "author": "user",
        "created": datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
        "eval_score": None,
        "eval_count": 0,
        "enabled": True,
        "errors": []
    }
    TOOLBOX_REG.write_text(json.dumps(registry, indent=2))
    
    logger.info(f"Installed external skill: {skill_name}")
    return f"✅ Skill '{skill_name}' installed from external directory."


def _generate_schemas_from_tools():
    """Generate tool schemas from TOOLS dict."""
    from ..tools import TOOLS, inspect
    schemas = []
    for name, info in TOOLS.items():
        func = info["func"]
        try:
            sig = inspect.signature(func)
        except ValueError:
            continue
            
        params = {}
        required = []
        for param_name, param in sig.parameters.items():
            if param_name in ("user_id", "_depth", "context"):
                continue
                
            ptype = "string"
            if param.annotation == int: ptype = "integer"
            elif param.annotation == bool: ptype = "boolean"
            elif param.annotation == float: ptype = "number"
            
            params[param_name] = {"type": ptype, "description": ""}
            if param.default == inspect.Parameter.empty:
                required.append(param_name)
                
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": info["desc"] or "",
                "parameters": {
                    "type": "object",
                    "properties": params,
                    "required": required
                }
            }
        })
    return schemas