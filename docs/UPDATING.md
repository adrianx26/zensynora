# Updating ZenSynora safely

ZenSynora updates **side by side**: the new version is downloaded and built in
its own folder while the current one keeps working. It is only switched in
after it has passed its checks against your configuration. Before anything
changes, **your data and the whole installed program folder are backed up**;
any failure puts the previous version back exactly as it was.

> Updated for v0.7.1: full program-folder backup and restore, settings/API
> keys/knowledge base checked and restored after the switch, approval-gated
> self-update proposals (see [Medic & Doctor](medic_agent_guide.md#6-self-update-proposals)).

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
| `update` *(default)* | Update to the latest `main`. `--ref v0.7.1` for a tag/branch, `--from-dir PATH` / `--from-archive FILE` for offline updates, `--run-tests` to run the test suite before switching, `--force` to reinstall the same version, `--app-backup full\|code\|none` for the program-folder backup (default `full`; `code` skips `venv/`). |
| `rollback` | Activate the previous version. Restores `~/.myclaw` too **only if** that update changed your data (`--restore-data` / `--keep-data` to override). `--to RELEASE` picks a specific one. |
| `status [--check] [--json]` | Installed version, available releases and backups, recent updates. |
| `backup [--label X]` | Back up `~/.myclaw` now (for selective backups use `zensynora backup create` or the Web UI **Backups** page). |
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
 4 backup data      ~/.myclaw + .env + data/ → backups/<time>-<from>-to-<to>/  (mode 700)
 5 backup program   the whole running release folder (code, venv, web UI, .env,
                    your own files) → app-folder.tar.gz + an inventory of every file
 6 carry-over       copy .env and data/ into the new release
 7 build            new venv, requirements, `pip install -e`, your extra packages, web UI
 8 verify           imports, load YOUR config with the new code, `zensynora --help`
 9 stop service     systemd `myclaw` (only if it was running)
10 migrate          versioned data migrations (see below)
11 activate         atomically repoint  source -> releases/<new>
12 restore data     settings, API keys and knowledge base checked; anything missing
                    is put back from the backup of step 4
13 start service    and confirm it stays up
14 finalize         record history, prune old releases/backups
```

Steps 1–10 never touch the running version. If any step fails, the completed
steps are undone in reverse order: the new release folder is deleted, data is
restored from the backup if a migration ran, `source` is pointed back, the
service is restarted — and the previous program folder is compared with the
inventory from step 5 and **restored from the archive if anything in it
differs** (changed, missing or extra files). The update log says what was
restored.

Program-folder archives are large (about the size of the folder,
compressed), so they are kept for the last two updates; data backups follow
`--keep-backups` (default 5). Backups you make yourself (`update.sh backup`,
`zensynora backup`, the Web UI) are never pruned automatically.

## Layout

```
~/.local/share/zensynora/
  source -> releases/0.6.0-20261013T203000   # what everything runs from
  releases/                                  # one complete copy per version (own venv)
  backups/                                   # data snapshots + program archives (contain API keys)
  staging/                                   # temporary copies for approved self-update proposals
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
data lives in the Docker volume and is not touched by this updater. Back it up
first with the Web UI **Backups** page or `docker compose exec zensynora
zensynora backup create`.

## Changes proposed by agents

Agents can propose changes to ZenSynora's own code. An approved proposal is
installed by this updater exactly like an update from a local folder
(`--from-dir` a copy of the current release with the change applied,
`--force`), so every safeguard above applies, and `rollback` undoes it.
Details: [Medic & Doctor → Self-update proposals](medic_agent_guide.md#6-self-update-proposals).

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
access; `tests/test_selfupdate.py` covers proposals going through it. For an end-to-end check, point `--base`/`--data-dir` at a scratch
folder and update between two local source trees with `--from-dir`.
