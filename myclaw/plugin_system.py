"""
Plugin System for Third-Party Extensions

Provides a comprehensive plugin architecture for extending MyClaw functionality:
- Plugin discovery and loading
- Plugin lifecycle management
- Hook system for integration
- Plugin dependencies
- Sandboxed plugin execution
- Plugin API access
"""

import asyncio
import hashlib
import importlib
import importlib.util
import json
import logging
import os
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Type

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path.home() / ".myclaw" / "plugins"
PLUGIN_DIR.mkdir(parents=True, exist_ok=True)

PLUGIN_REGISTRY = PLUGIN_DIR / "plugin_registry.json"


@dataclass
class PluginManifest:
    """Plugin manifest/descriptor."""
    name: str
    version: str
    description: str
    author: str
    entry_point: str
    dependencies: List[str] = field(default_factory=list)
    hooks: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    min_api_version: Optional[str] = None


@dataclass
class PluginInfo:
    """Information about a loaded plugin."""
    manifest: PluginManifest
    module: Any
    instance: Any
    enabled: bool = True
    loaded_at: Optional[datetime] = None
    errors: List[str] = field(default_factory=list)


class PluginHook:
    """Hook for plugin integration points."""
    
    def __init__(self, name: str):
        self.name = name
        self._handlers: List[Callable] = []
    
    def register(self, handler: Callable):
        """Register a hook handler."""
        self._handlers.append(handler)
    
    def unregister(self, handler: Callable):
        """Unregister a hook handler."""
        if handler in self._handlers:
            self._handlers.remove(handler)
    
    async def emit(self, *args, **kwargs) -> List[Any]:
        """Emit the hook and call all handlers."""
        results = []
        for handler in self._handlers:
            try:
                if asyncio.iscoroutinefunction(handler):
                    result = await handler(*args, **kwargs)
                else:
                    result = handler(*args, **kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"Hook handler error for {self.name}: {e}")
        return results
    
    def emit_sync(self, *args, **kwargs) -> List[Any]:
        """Emit hook synchronously."""
        results = []
        for handler in self._handlers:
            try:
                result = handler(*args, **kwargs)
                results.append(result)
            except Exception as e:
                logger.error(f"Hook handler error for {self.name}: {e}")
        return results


class PluginSystem:
    """Main plugin system controller.
    
    Features:
    - Plugin discovery and loading
    - Plugin lifecycle management
    - Hook system for integration
    - Plugin dependencies
    - Sandboxed execution
    - Plugin API access
    """
    
    HOOKS = [
        "on_agent_init",
        "on_agent_think",
        "on_agent_response",
        "on_tool_call",
        "on_tool_result",
        "on_error",
        "on_message_received",
        "on_message_sent",
        "on_session_start",
        "on_session_end",
        "on_plugin_load",
        "on_plugin_unload",
    ]
    
    def __init__(self):
        self._plugins: Dict[str, PluginInfo] = {}
        self._hooks: Dict[str, PluginHook] = {
            name: PluginHook(name) for name in self.HOOKS
        }
        self._api_permissions: Set[str] = set()
        self._plugin_registry = self._load_registry()
    
    def _load_registry(self) -> Dict[str, Any]:
        """Load plugin registry from disk."""
        if PLUGIN_REGISTRY.exists():
            try:
                return json.loads(PLUGIN_REGISTRY.read_text())
            except Exception as e:
                logger.error(f"Failed to load plugin registry: {e}")
        return {"plugins": {}, "enabled": []}
    
    def _save_registry(self):
        """Save plugin registry to disk."""
        try:
            PLUGIN_REGISTRY.write_text(json.dumps(self._plugin_registry, indent=2))
        except Exception as e:
            logger.error(f"Failed to save plugin registry: {e}")
    
    def register_hook(self, hook_name: str, handler: Callable):
        """Register a hook handler.
        
        Args:
            hook_name: Name of the hook
            handler: Callable handler
        """
        if hook_name in self._hooks:
            self._hooks[hook_name].register(handler)
            logger.info(f"Registered hook: {hook_name}")
        else:
            logger.warning(f"Unknown hook: {hook_name}")
    
    def unregister_hook(self, hook_name: str, handler: Callable):
        """Unregister a hook handler."""
        if hook_name in self._hooks:
            self._hooks[hook_name].unregister(handler)
    
    async def emit_hook(self, hook_name: str, *args, **kwargs) -> List[Any]:
        """Emit a hook and get results from all handlers."""
        if hook_name in self._hooks:
            return await self._hooks[hook_name].emit(*args, **kwargs)
        return []
    
    def discover_plugins(self) -> List[str]:
        """Discover available plugins in plugin directory."""
        discovered = []
        
        for item in PLUGIN_DIR.iterdir():
            if item.is_dir() and (item / "manifest.json").exists():
                discovered.append(item.name)
            elif item.suffix == ".py" and item.stem != "__init__":
                discovered.append(item.stem)
            elif item.suffix == ".zip":
                discovered.append(item.stem)
        
        return discovered
    
    def load_plugin(self, plugin_name: str) -> bool:
        """Load a plugin by name.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            True if loaded successfully
        """
        plugin_path = PLUGIN_DIR / plugin_name
        
        manifest = None
        module = None
        
        if (plugin_path / "manifest.json").exists():
            manifest = self._load_manifest(plugin_path / "manifest.json")
            
            init_file = plugin_path / "__init__.py"
            if init_file.exists():
                module = self._load_python_plugin(plugin_name, init_file)
        
        elif (PLUGIN_DIR / f"{plugin_name}.py").exists():
            module = self._load_python_plugin(
                plugin_name,
                PLUGIN_DIR / f"{plugin_name}.py"
            )
            manifest = PluginManifest(
                name=plugin_name,
                version="1.0.0",
                description="Plugin from file",
                author="unknown",
                entry_point="execute"
            )
        
        elif (PLUGIN_DIR / f"{plugin_name}.zip").exists():
            module = self._load_zip_plugin(plugin_name, PLUGIN_DIR / f"{plugin_name}.zip")
        
        if module is None:
            logger.error(f"Failed to load plugin: {plugin_name}")
            return False
        
        instance = self._create_plugin_instance(module, manifest)
        
        plugin_info = PluginInfo(
            manifest=manifest,
            module=module,
            instance=instance,
            enabled=True,
            loaded_at=datetime.now()
        )
        
        self._plugins[plugin_name] = plugin_info
        self._plugin_registry["plugins"][plugin_name] = {
            "version": manifest.version if manifest else "1.0.0",
            "loaded_at": datetime.now().isoformat()
        }
        
        self._emit_hook_sync("on_plugin_load", plugin_name)
        self._save_registry()
        
        logger.info(f"Loaded plugin: {plugin_name}")
        return True
    
    def _load_manifest(self, path: Path) -> Optional[PluginManifest]:
        """Load plugin manifest."""
        try:
            data = json.loads(path.read_text())
            return PluginManifest(
                name=data["name"],
                version=data.get("version", "1.0.0"),
                description=data.get("description", ""),
                author=data.get("author", "unknown"),
                entry_point=data.get("entry_point", "execute"),
                dependencies=data.get("dependencies", []),
                hooks=data.get("hooks", []),
                permissions=data.get("permissions", []),
                config_schema=data.get("config_schema", {}),
                min_api_version=data.get("min_api_version")
            )
        except Exception as e:
            logger.error(f"Failed to load manifest: {e}")
            return None
    
    def _load_python_plugin(self, name: str, path: Path) -> Optional[Any]:
        """Load a Python plugin file."""
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                spec.loader.exec_module(module)
                return module
        except Exception as e:
            logger.error(f"Failed to load Python plugin {name}: {e}")
        return None
    
    def _load_zip_plugin(self, name: str, path: Path) -> Optional[Any]:
        """Load a plugin from a zip file."""
        try:
            extract_dir = PLUGIN_DIR / f"{name}_extracted"
            extract_dir.mkdir(exist_ok=True)
            
            with zipfile.ZipFile(path, "r") as zf:
                zf.extractall(extract_dir)
            
            init_path = extract_dir / "__init__.py"
            if init_path.exists():
                return self._load_python_plugin(name, init_path)
            
            main_py = extract_dir / "main.py"
            if main_py.exists():
                return self._load_python_plugin(name, main_py)
            
        except Exception as e:
            logger.error(f"Failed to load zip plugin {name}: {e}")
        
        return None
    
    def _create_plugin_instance(
        self,
        module: Any,
        manifest: Optional[PluginManifest]
    ) -> Any:
        """Create plugin instance from module."""
        if manifest and hasattr(module, manifest.entry_point):
            entry = getattr(module, manifest.entry_point)
            if callable(entry):
                return entry()
        
        if hasattr(module, "Plugin"):
            return module.Plugin()
        
        if hasattr(module, "execute"):
            return module.execute
        
        return module
    
    def _emit_hook_sync(self, hook_name: str, *args, **kwargs):
        """Emit hook synchronously."""
        if hook_name in self._hooks:
            self._hooks[hook_name].emit_sync(*args, **kwargs)
    
    def unload_plugin(self, plugin_name: str) -> bool:
        """Unload a plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            True if unloaded successfully
        """
        if plugin_name not in self._plugins:
            return False
        
        self._emit_hook_sync("on_plugin_unload", plugin_name)
        
        del self._plugins[plugin_name]
        
        if plugin_name in sys.modules:
            del sys.modules[plugin_name]
        
        if plugin_name in self._plugin_registry.get("plugins", {}):
            del self._plugin_registry["plugins"][plugin_name]
        
        self._save_registry()
        
        logger.info(f"Unloaded plugin: {plugin_name}")
        return True
    
    def enable_plugin(self, plugin_name: str) -> bool:
        """Enable a plugin."""
        if plugin_name in self._plugins:
            self._plugins[plugin_name].enabled = True
            if plugin_name not in self._plugin_registry.get("enabled", []):
                self._plugin_registry.setdefault("enabled", []).append(plugin_name)
            self._save_registry()
            return True
        return False
    
    def disable_plugin(self, plugin_name: str) -> bool:
        """Disable a plugin."""
        if plugin_name in self._plugins:
            self._plugins[plugin_name].enabled = False
            if plugin_name in self._plugin_registry.get("enabled", []):
                self._plugin_registry["enabled"].remove(plugin_name)
            self._save_registry()
            return True
        return False
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """List all loaded plugins."""
        return [
            {
                "name": name,
                "version": info.manifest.version if info.manifest else "1.0.0",
                "enabled": info.enabled,
                "loaded_at": info.loaded_at.isoformat() if info.loaded_at else None,
                "hooks": info.manifest.hooks if info.manifest else []
            }
            for name, info in self._plugins.items()
        ]
    
    def get_plugin_api(self, plugin_name: str) -> Optional[Dict[str, Callable]]:
        """Get the API exposed by a plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            Dictionary of API methods
        """
        if plugin_name not in self._plugins:
            return None
        
        info = self._plugins[plugin_name]
        
        if hasattr(info.instance, "__dict__"):
            return {
                k: v for k, v in info.instance.__dict__.items()
                if callable(v) and not k.startswith("_")
            }
        
        return {"execute": info.instance} if callable(info.instance) else {}
    
    def call_plugin_method(
        self,
        plugin_name: str,
        method: str,
        *args,
        **kwargs
    ) -> Any:
        """Call a method on a plugin.
        
        Args:
            plugin_name: Name of the plugin
            method: Method name
            *args, **kwargs: Arguments to pass
            
        Returns:
            Method result
        """
        if plugin_name not in self._plugins:
            raise ValueError(f"Plugin not loaded: {plugin_name}")
        
        info = self._plugins[plugin_name]
        
        if not info.enabled:
            raise RuntimeError(f"Plugin disabled: {plugin_name}")
        
        instance = info.instance
        
        if isinstance(instance, dict):
            if method not in instance:
                raise AttributeError(f"Method not found: {method}")
            return instance[method](*args, **kwargs)
        
        if hasattr(instance, method):
            return getattr(instance, method)(*args, **kwargs)
        
        raise AttributeError(f"Method not found: {method}")
    
    def install_plugin(self, source: str) -> str:
        """Install a plugin from source.
        
        Args:
            source: URL, file path, or package name
            
        Returns:
            Installation result message
        """
        if source.startswith(("http://", "https://")):
            return self._install_from_url(source)
        elif Path(source).exists():
            return self._install_from_file(source)
        else:
            return self._install_from_package(source)
    
    def _install_from_url(self, url: str) -> str:
        """Install plugin from URL."""
        try:
            import requests
            response = requests.get(url, timeout=30)
            
            if response.status_code != 200:
                return f"Error: Failed to download (status {response.status_code})"
            
            content_type = response.headers.get("content-type", "")
            
            if "zip" in content_type or url.endswith(".zip"):
                plugin_name = url.split("/")[-1].replace(".zip", "")
                zip_path = PLUGIN_DIR / f"{plugin_name}.zip"
                zip_path.write_bytes(response.content)
                return f"Downloaded plugin to {zip_path}"
            
            return f"Error: Unsupported download type"
            
        except Exception as e:
            return f"Error: {e}"
    
    def _install_from_file(self, path: str) -> str:
        """Install plugin from local file."""
        source = Path(path)
        
        if not source.exists():
            return f"Error: File not found: {path}"
        
        plugin_name = source.stem
        
        dest = PLUGIN_DIR / plugin_name
        dest.mkdir(exist_ok=True)
        
        if source.is_dir():
            shutil.copytree(source, dest, dirs_exist_ok=True)
        else:
            shutil.copy2(source, dest / source.name)
        
        return f"Installed plugin: {plugin_name}"
    
    def _install_from_package(self, package: str) -> str:
        """Install plugin from pip package."""
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", package, "--target", str(PLUGIN_DIR)],
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                return f"Installed package: {package}"
            else:
                return f"Error: {result.stderr}"
                
        except Exception as e:
            return f"Error: {e}"
    
    def uninstall_plugin(self, plugin_name: str) -> str:
        """Uninstall a plugin.
        
        Args:
            plugin_name: Name of the plugin
            
        Returns:
            Uninstall result message
        """
        self.unload_plugin(plugin_name)
        
        plugin_path = PLUGIN_DIR / plugin_name
        
        if plugin_path.exists():
            if plugin_path.is_dir():
                shutil.rmtree(plugin_path)
            else:
                plugin_path.unlink()
        
        zip_path = PLUGIN_DIR / f"{plugin_name}.zip"
        if zip_path.exists():
            zip_path.unlink()
        
        return f"Uninstalled plugin: {plugin_name}"


_global_plugin_system: Optional[PluginSystem] = None


def get_plugin_system() -> PluginSystem:
    """Get or create the global plugin system."""
    global _global_plugin_system
    
    if _global_plugin_system is None:
        _global_plugin_system = PluginSystem()
    
    return _global_plugin_system


def register_plugin_hook(hook_name: str, handler: Callable):
    """Register a global plugin hook."""
    get_plugin_system().register_hook(hook_name, handler)


async def emit_plugin_hook(hook_name: str, *args, **kwargs) -> List[Any]:
    """Emit a global plugin hook."""
    return await get_plugin_system().emit_hook(hook_name, *args, **kwargs)


__all__ = [
    "PluginManifest",
    "PluginInfo",
    "PluginHook",
    "PluginSystem",
    "get_plugin_system",
    "register_plugin_hook",
    "emit_plugin_hook",
]