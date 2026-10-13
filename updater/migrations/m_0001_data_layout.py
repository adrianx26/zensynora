"""Baseline for 0.5.0: make sure the standard data folders exist and the
config file (if any) is private to the user."""

import os

ID = "0001_data_layout"
DESCRIPTION = "Create standard ~/.myclaw folders, restrict config permissions"
REVERSIBLE = True

FOLDERS = [
    "workspace", "knowledge", "tools", "skills", "backups", "task_logs", "benchmarks",
    "newtech", "semantic_cache", "sandbox", "audit", "medic", "logs",
    "plugins", "plugins/installed", "hub", "hub/skills", "api",
]


def apply(ctx):
    for name in FOLDERS:
        (ctx.data_dir / name).mkdir(parents=True, exist_ok=True)
    for secret in ("config.json", ".config_key"):
        p = ctx.data_dir / secret
        if p.exists():
            os.chmod(p, 0o600)
