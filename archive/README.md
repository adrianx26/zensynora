# Archive Folder

This directory holds files that were part of the original source tree but are no longer used in the production code.

## Why `tools_backup.py` was moved here
* It is a **legacy copy** of the toolbox utilities that now live in `myclaw/tools/toolbox.py`.
* No module in the codebase imports anything from `myclaw/tools_backup.py`; the static analysis showed **zero internal references**.
* Keeping it in the live package adds noise and the risk that a future contributor might edit the backup instead of the actual toolbox.

By moving the file to `archive/` we:
1. Preserve the historical source for reference.
2. Remove it from the import path so it can’t be accidentally used.
3. Keep the repository tidy.

If you need to consult the old implementation, it remains available in this folder.
