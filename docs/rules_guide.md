# Rules: what every agent must follow

Rules are short instructions from you that every agent follows in everything it
does: *answer in Romanian*, *never delete files without asking*, *cite the pages
you used*. You write and change them; agents can only **propose** changes, and
nothing changes until you agree.

## Where to find them

* **Web UI → Rules** (📜 in the sidebar). The number next to it is the count of
  proposals waiting for you.
* **Terminal**: `zensynora rules list | show SLUG | proposals | approve ID | reject ID | accept SLUG | restore SLUG`.
* **Files**: `~/.myclaw/rules/<slug>.md`. You can edit them with any editor, but
  a file changed outside ZenSynora needs **one click to activate** (see below).

## Rules changed outside ZenSynora

ZenSynora remembers a fingerprint of every rule you saved or approved
(`~/.myclaw/rules/.manifest.json`, written only by your own actions). A rule
file that does not match — edited by hand, copied in, or changed by anything
else — is **not given to agents** until you look at it. The Rules page shows it
as *Changed outside ZenSynora — review* with the difference to the version you
last approved, and two buttons:

* **Keep this version** — use the file as it is now (`zensynora rules accept SLUG`);
* **Restore approved version** — put back what you approved; the changed file
  is kept in `.trash/` (`zensynora rules restore SLUG`).

Symbolic links and files with other names (e.g. `My Rule.md`) are never used;
the doctor lists them.

## Writing a rule

**+ New rule** on the Rules page:

| Field | Meaning |
|---|---|
| Title | A short name, e.g. *Answer in Romanian*. The file name (slug) is made from it: `answer-in-romanian`. |
| Priority | `high`, `normal` or `low`. Higher rules come first in the agents' instructions and win in a conflict. |
| Applies to | *all agents*, or only the agents you pick. |
| Rule | What agents must do or not do (Markdown, at most 3000 characters). |
| Enabled | Switched-off rules are kept but agents do not see them. |

A rule file looks like this:

```markdown
---
title: Answer in Romanian
scope: all
priority: high
enabled: true
author: user
created: 2026-10-27T10:00:00
updated: 2026-10-27T10:00:00
---

Always answer in Romanian, even when the question is in English.
```

`scope` is `all` or agent names separated by commas (`Researcher, default`).
`author` is `user` or `agent:<name>` when an agent proposed the rule and you
approved it.

Never put passwords, tokens or API keys in a rule: rules are sent to the model
with every message, so ZenSynora refuses text that looks like a secret.

## What agents see

Every agent — in chat, in jobs, when another agent delegates to it, on Telegram —
gets a section **"Rules you must follow"** in its instructions with the enabled
rules that apply to it, highest priority first. The rules are labelled as your
own instructions. At most 4000 characters of rules are included (Settings →
Features → Rules); when some do not fit, the agent is told and can read them
with `list_rules` / `read_rule`. Proposals are never shown to agents as rules.

## Proposals from agents

Agents have these tools:

| Tool | What it does |
|---|---|
| `list_rules()` | the rules and the agent's own proposals that wait for you |
| `read_rule(slug)` | one rule in full |
| `propose_rule(title, text, scope, reason)` | propose a new rule |
| `propose_rule_change(slug, text, reason)` | propose new text for a rule |
| `propose_rule_deletion(slug, reason)` | propose deleting a rule |

Each proposal answers *"Proposal #N sent to the user for approval — it is not
active yet."* It appears at the top of the Rules page with who proposed it, why,
and a line diff against the rule as it is now. **Approve** applies it at once;
**Reject** keeps it in the history (with your note). If the Telegram bot runs in
the Web UI, allowed Telegram users get a message about each new proposal (turn
it off in Settings → Features → Rules).

Limits: an agent may have at most 5 proposals waiting (setting
`rules.max_pending_per_agent`); the same change cannot be proposed twice. If
you changed a rule after an agent proposed a change to it, the proposal is
marked *changed after this proposal was made* and the diff is against your
newer text.

### Only you can approve

Agents cannot activate a rule by other means either:

* approving, rejecting and changing rules in the Web UI needs a header that only
  the Web UI sends, and requests coming from other web sites are refused;
* the shell tool refuses arguments that point into `~/.myclaw` outside the
  workspace (rules, settings, proposals), commands that write (`git`, `uniq`,
  `curl`, `wget`) may only write inside the workspace, and `sort -o`, git's
  `-C`/`-c`/`--git-dir`/`--work-tree` and `git config` changes are refused;
* `curl`/`wget` (only when you allowed them in Settings → Security) may fetch
  only public web addresses — never this computer or the local network;
* tools agents build with `register_tool` cannot import ZenSynora's own code;
* a rule file written by anything else is not used until you review it (above).

## Nothing is lost

When a rule is changed or deleted — by you or by an approved proposal — the
previous file is copied to `~/.myclaw/rules/.trash/` first. Proposals stay in
`~/.myclaw/rules/.proposals/` as history (the newest 500 decided ones). The rules folder is part of backups
(*Jobs, own tools, skills & rules*).

## Doctor

`zensynora doctor` (and the Doctor page) shows *Rules: N active, M proposal(s)
waiting* and warns when a proposal has waited more than 7 days, when a rule was
changed outside ZenSynora, or when a file in the folder is not used (wrong
name, symbolic link, unreadable).

## Settings (Settings → Features → Rules)

| Setting | Default | Meaning |
|---|---|---|
| `rules.enabled` | on | add the rules to the agents' instructions |
| `rules.prompt_chars` | 4000 | most characters of rules per prompt (500–20000) |
| `rules.max_pending_per_agent` | 5 | proposals one agent may have waiting |
| `rules.notify_telegram` | on | tell allowed Telegram users about new proposals |
