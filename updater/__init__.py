"""ZenSynora safe updater (stdlib only — runs even if the app's venv is broken).

Layout it manages (``BASE`` defaults to ``~/.local/share/zensynora``)::

    BASE/
      source -> releases/0.5.1-20261013T201500   (symlink = the active release)
      releases/<version>-<timestamp>/            (one self-contained copy per version,
                                                  each with its own venv/ and webui/dist)
      backups/<timestamp>-<from>-to-<to>/        (user-data snapshot, full program-folder
                                                  archive + inventory, manifest)
      logs/update-<timestamp>.log
      updater-state.json                         (history used for rollback)

User data stays in ``~/.myclaw`` and is shared by all releases; it is backed up
before every update and only changed by versioned migrations.

An update is a pipeline of small steps (see ``steps.py``). Every step can undo
itself, so a failure at any point leaves the previous working release active.
"""

# Bump when the updater's own behaviour changes in a way a newer release should
# take over (an older installed updater hands off to a newer downloaded one).
# 2 (v0.7.1): full program-folder backup + restore on failure, data put back
#   in place after the switch, manual backups never pruned.
UPDATER_PROTOCOL = 2

DEFAULT_REPO = "adrianx26/zensynora"
DEFAULT_REF = "main"
