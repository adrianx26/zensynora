"""Re-index every knowledge base so links by title/alias are stored (2026-10-23, KB phase 0).

Until KB phase 0 a link ``- uses [[Home server]]`` reached the index only when
its target was written by permalink (``home-server``) and already existed, and
re-indexing a note deleted the links pointing *to* it. The new index resolves
links by permalink, title, name or alias, keeps links to notes that do not
exist yet, and stores aliases, metadata and frontmatter tags. This migration
rebuilds each base's index from the markdown files (the files are the source
of truth and are not changed). ``.trash`` folders are not indexed.

Safe to run twice: a forced sync always rebuilds the same index from the files.
"""

ID = "0003_knowledge_reindex_links"
DESCRIPTION = "Rebuild the knowledge index so links by title/alias, aliases and metadata are stored"
REVERSIBLE = True  # only the index (rebuildable) changes; notes are untouched


def apply(ctx):
    root = ctx.data_dir / "knowledge"
    if not root.is_dir():
        return
    for base in sorted(p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")):
        if not any(base.rglob("*.md")):
            continue
        try:
            from myclaw.knowledge.sync import sync_knowledge

            stats = sync_knowledge(base.name, force=True)
            ctx.log.info("  knowledge/%s re-indexed: %s notes, %s errors",
                         base.name, stats.get("added", 0), stats.get("errors", 0))
        except Exception as e:  # the index can be rebuilt later; never block the update
            ctx.log.warning("  could not re-index knowledge/%s (run: zensynora knowledge sync --force): %s",
                            base.name, e)
