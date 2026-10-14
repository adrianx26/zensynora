---
name: web-research
description: Look up current facts on the web (news, prices, weather, documentation) with the browse tool and say where they came from.
---

# Web research

Use this when the answer depends on what is true now, or on a page you have not read.

## Steps

1. **Pick a direct source first.** Go straight to a page that has the answer instead of a search
   results page when you can:
   - Weather: `https://wttr.in/<City>?format=3` (one line) or `https://wttr.in/<City>?format=j1` (details).
   - Wikipedia: `https://en.wikipedia.org/wiki/<Title>`.
   - Search, when you must: `https://html.duckduckgo.com/html/?q=<words+joined+with+plus>`.
2. **browse(url)** once. Read what came back before calling another tool.
3. If the page failed (error, empty, blocked), try **one** other source — do not repeat the same URL.
4. Stop as soon as you have the answer. Three to four tool calls are enough for most questions.

## Answer

- Give the answer first, in a sentence or a short list.
- Name the source (site name and URL) and, for things that change, when it was read.
- If sources disagree or you could not confirm something, say so plainly. Never invent numbers.
