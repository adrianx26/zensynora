# Updating ZenSynora safely

ZenSynora updates **side by side**: the new version is downloaded and built in
its own folder while the current one keeps working. It is only switched in
after it has passed its checks against your configuration, and your data is
backed up first. Any failure leaves the previous version active.

## Everyday use

```bash
zensynora update                  # update to the latest version (GitHub main)
zensynora update status --check   # what's installed, is a newer version out?
zensynora update rollback         # go back to the previous version
```

If the `zensynora` command isn't available, use the script directly:
`bash ~/.local/share/zensynora/source/update.sh` (same options).

| Command | What it does |
|---|---|
| `update` *(default)* | Update to the latest `main`. `--ref v0.7.0` for a tag/branch, `--from-dir PATH` / `--from-archive FILE` for offline updates, `--run-tests` to run the test suite before switching, `--force` to reinstall the same version. |
| `rollback` | Activate the previous version. Restores `~/.myclaw` too **only if** that update changed your data (`--restore-data` / `--keep-data` to override). `--to RELEASE` picks a specific one. |
| `status [--check] [--json]` | Installed version, available releases and backups, recent updates. |
| `backup [--label X]` | Back up `~/.myclaw` now. |
| `restore --backup NAME` | Restore `~/.myclaw` from a backup (current data is saved first). |
| `prune` | Delete old releases/backups (defaults: keep 3 releases, 5 backups). |

**Upgrading from an install made before 0.6.0** (no `update.sh` yet): run the
online installer once — it detects the existing install and runs the updater:

```bash
curl -fsSL https://raw.githubusercontent.com/adrianx26/zensynora/main/install-online.sh | bash
```

## What happens during an update

```
 1 preflight        Python ≥ 3.11, ~1.5 GB free, find current version, service running?
 2 fetch            download/copy new source → releases/<version>-<time>/
 3 handoff          if the new version ships a newer updater, it takes over
 4 backup           ~/.myclaw + .env + data/ → backups/<time>-<from>-to-<to>/  (mode 700)
 5 carry-over       copy .env and data/ into the new release
 6 build            new venv, requirements, `pip install -e`, your extra packages, web UI
 7 verify           imports, load YOUR config with the new code, `zensynora --help`
 8 stop service     systemd `myclaw` (only if it was running)
 9 migrate          versioned data migrations (see below)
10 activate         atomically repoint  source -> releases/<new>
11 start service    and confirm it stays up
12 finalize         record history, prune old releases/backups
```

Steps 1–9 never touch the running version. If any step fails, the completed
steps are undone in reverse order (new release folder deleted, data restored
from the backup if a migration ran, `source` pointed back, service restarted).

## Layout

```
~/.local/share/zensynora/
  source -> releases/0.6.0-20261013T203000   # what everything runs from
  releases/                                  # one complete copy per version (own venv)
  backups/                                   # user-data snapshots (contain API keys)
  logs/update-*.log                          # full log of every update
  updater-state.json                         # history used by rollback
~/.myclaw/                                   # your data, shared by all versions
  .migrations.json                           # which data migrations have run
```

Paths through `source/` (systemd unit, `source/venv/bin/activate`) keep
working across updates because `source` is a symlink. The first update of an
older in-place install moves it to `releases/<version>-legacy-…` so it stays
available for rollback.

Docker deployments update by rebuilding the image (`install.sh --docker`);
data lives in the Docker volume and is not touched by this updater.

## For developers: extending the update procedure

The updater (`updater/`) uses **only the Python standard library**, so it runs
even when the app's own environment is broken.

### Add a data migration

When a release changes the format of anything in `~/.myclaw` (config keys,
database schema, folder layout), add `updater/migrations/m_NNNN_name.py`:

```python
ID = "0002_rename_provider_key"          # unique, sortable, never reused
DESCRIPTION = "Rename providers.llama -> providers.llamacpp"
REVERSIBLE = False                       # informational

def apply(ctx):                          # ctx.data_dir, ctx.app_version, ctx.log
    cfg = ctx.data_dir / "config.json"
    ...                                  # must be safe to run more than once
```

Migrations run once each, in ID order, inside the **new** release's
environment (so they may `import myclaw`), after the backup and before the
switch. A failing migration aborts the update and the backup is restored.
Fresh installs record all current migrations as applied (`install.sh` runs
`python -m updater migrate`).

### Add or change an update step

Steps live in `updater/steps.py`; each is a class with `run(ctx)` and
`rollback(ctx)`, and the order is `default_pipeline()`. To add behaviour
(e.g. "pull a new Ollama model"), write a `Step`, make its `rollback` undo
it, and insert it in the list. `Context` carries everything steps share.

### Change the updater itself

Users run the updater of the version they have installed. If a newer release
needs different update logic, bump `UPDATER_PROTOCOL` in
`updater/__init__.py`; older updaters then hand over to the newly downloaded
one before doing anything else.

### Test

`tests/test_updater.py` covers the pipeline rollback, backups, migrations,
adoption of legacy installs, version policy and pruning without network
access. For an end-to-end check, point `--base`/`--data-dir` at a scratch
folder and update between two local source trees with `--from-dir`.
