---
name: knowledge-keeper
description: Decide what is worth remembering and save it to the knowledge base so later answers and other agents can use it.
---

# Knowledge keeper

ZenSynora has a shared knowledge base: notes with text, facts (observations), links to other
notes and tags. Use it so that useful facts are not lost when the conversation ends.

## Before answering

- For questions about the user, their projects, settings or earlier decisions, call
  `search_knowledge` first and use what you find. It understands `AND`, `OR`, `NOT`,
  "quoted phrases" and `tag:name`; open a whole note with `read_knowledge(permalink)`.

## When to save (write_to_knowledge)

Save facts that will matter later:
- the user's preferences ("prefers Python, C# and PowerShell"), names, places, recurring tasks;
- decisions and their reasons; results of jobs or research that took effort;
- fixes that worked ("LM Studio needs /v1 at the end of the URL").

Do **not** save small talk, things already in a note, or the current state of the system
(health reports, error counts, file listings) — it is out of date minutes later.
**Never** save secrets: passwords, API keys, tokens, private keys or other credentials.

## How

- One note per topic, a clear title, the details as text, short observations, 2-4 tags.
- Link related notes: a line `- relation [[Other note]]` in the text (or `relations`).
- Update an existing note (search first) instead of creating a near-duplicate.
- Tell the user in one short line what you saved.
