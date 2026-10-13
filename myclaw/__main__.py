"""Allow `python -m myclaw …` (same as the `zensynora` command).

Added 2026-10-13 (v0.7.0) for the portable launchers and environments where the
console-script wrapper is not on PATH.
"""
from myclaw.cli import cli

if __name__ == "__main__":
    cli()
