"""
Hardware Awareness Module for ZenSynora.
Collects detailed system metrics for CPU, GPU, RAM, NPU, and Network.
"""

import os
import platform
import shutil
import psutil
import logging
import subprocess
import json
import cpuinfo
try:
    import GPUtil
except Exception:
    GPUtil = None
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def _safe_run_command(command: List[str]) -> str:
    """Run a command without shelling out and return stdout safely."""
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode == 0:
            return completed.stdout or ""
    except (OSError, ValueError):
        return ""
    return ""


def get_system_metrics() -> Dict[str, Any]:
    """Collect all available system hardware metrics."""
    metrics = {
        "cpu": _get_cpu_metrics(),
        "memory": _get_memory_metrics(),
        "gpu": _get_gpu_metrics(),
        "npu": _get_npu_metrics(),
        "network": _get_network_metrics(),
        "os": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine()
        }
    }
    return metrics

def _get_cpu_metrics() -> Dict[str, Any]:
    """Get detailed CPU information."""
    info = cpuinfo.get_cpu_info()
    
    # Get temperatures
    temp = None
    try:
        temps = psutil.sensors_temperatures()
        if 'coretemp' in temps:
            temp = temps['coretemp'][0].current
        elif 'cpu_thermal' in temps:
            temp = temps['cpu_thermal'][0].current
    except Exception:
        pass

    return {
        "model": info.get("brand_raw", "Unknown"),
        "arch": info.get("arch", "Unknown"),
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_threads": psutil.cpu_count(logical=True),
        "frequency_mhz": psutil.cpu_freq().current if psutil.cpu_freq() else 0,
        "temperature_c": temp,
        "usage_pct": psutil.cpu_percent(interval=0.1)
    }

def _get_memory_metrics() -> Dict[str, Any]:
    """Get RAM details including type if possible."""
    mem = psutil.virtual_memory()
    mem_type = "Unknown"

    # Try to detect memory type (OS specific)
    try:
        if platform.system() == "Windows":
            powershell = shutil.which("powershell.exe") or shutil.which("powershell")
            wmic = shutil.which("wmic") or shutil.which("wmic.exe")

            output = ""
            if wmic:
                output = _safe_run_command([wmic, "memorychip", "get", "speed, manufacturer"])
            elif powershell:
                output = _safe_run_command([
                    powershell,
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    "Get-CimInstance Win32_PhysicalMemory -ErrorAction SilentlyContinue | Select-Object -First 1 | ForEach-Object { $_.Manufacturer + ' ' + $_.Speed + 'MHz' }",
                ])

            if output:
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                if len(lines) > 1:
                    mem_type = f"High Speed RAM ({lines[-1]})"
        elif platform.system() == "Linux":
            # dmidecode requires sudo, often fails for non-root
            if os.path.exists("/sys/class/dmi/id/board_name"):
                mem_type = "Detected"
    except Exception:
        pass

    return {
        "total_gb": round(mem.total / (1024**3), 2),
        "available_gb": round(mem.available / (1024**3), 2),
        "used_pct": mem.percent,
        "type": mem_type
    }

def _get_gpu_metrics() -> List[Dict[str, Any]]:
    """Get NVIDIA GPU information via GPUtil."""
    gpus_info = []
    try:
        if GPUtil is not None:
            gpus = GPUtil.getGPUs()
            for gpu in gpus:
                gpus_info.append({
                    "model": gpu.name,
                    "memory_total_mb": gpu.memoryTotal,
                    "memory_used_mb": gpu.memoryUsed,
                    "temperature_c": gpu.temperature,
                    "load_pct": gpu.load * 100,
                    "driver": gpu.driver
                })
    except Exception as e:
        logger.debug(f"GPU detection failed/skipped: {e}")
        
    # Check for Apple Silicon GPU
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        gpus_info.append({
            "model": "Apple M-Series Unified",
            "type": "Integrated (Unified Memory)"
        })
        
    return gpus_info

def _get_npu_metrics() -> Dict[str, Any]:
    """Check for NPU presence (DirectML, CoreML, etc)."""
    npu_status = {
        "present": False,
        "type": "None",
        "active": False
    }
    
    try:
        if platform.system() == "Windows":
            # Check for DirectML devices in device manager or specific DLLs
            if os.path.exists("C:\\Windows\\System32\\DirectML.dll"):
                npu_status["present"] = True
                npu_status["type"] = "DirectML compatible (NPU/GPU)"
        elif platform.system() == "Darwin":
            npu_status["present"] = True
            npu_status["type"] = "Apple Neural Engine"
            npu_status["active"] = True
    except Exception:
        pass
        
    return npu_status

def _get_network_metrics() -> Dict[str, Any]:
    """Get network latency and interface speed."""
    # Basic ping check for lag
    lag = -1
    try:
        host = "8.8.8.8"
        param = "-n" if platform.system() == "Windows" else "-c"
        command = ["ping", param, "1", host]
        output = subprocess.check_output(command).decode()
        if platform.system() == "Windows":
            if "Average =" in output:
                lag = int(output.split("Average =")[-1].strip().replace("ms", ""))
        else:
            if "time=" in output:
                lag = float(output.split("time=")[-1].split(" ")[0])
    except Exception:
        pass

    return {
        "ping_ms": lag,
        "interfaces": list(psutil.net_if_addrs().keys())
    }

def get_optimization_suggestions(metrics: Dict[str, Any]) -> List[str]:
    """Generate suggestions based on hardware."""
    suggestions = []
    ram_gb = metrics["memory"]["total_gb"]
    
    if ram_gb < 8:
        suggestions.append("⚠️ Low RAM detected. Recommended: Use models <= 3.8B parameters (e.g., Llama 3.2 3B).")
    elif ram_gb < 16:
        suggestions.append("💡 Moderate RAM. Recommended: Use models <= 8B parameters (e.g., Llama 3.1 8B).")
    else:
        suggestions.append("🚀 High RAM detected. You can comfortably run 8B-14B models or small swarms.")

    if not metrics["gpu"]:
        suggestions.append("ℹ️ No dedicated GPU detected. Ollama will run in CPU-only mode (slower).")
    else:
        vram = metrics["gpu"][0].get("memory_total_mb", 0)
        if vram > 0 and vram < 6000:
            suggestions.append(f"⚠️ Low VRAM ({vram}MB). Large models may experience significant offloading lag.")

    return suggestions
