# ZenSynora New Tech Agent Guide

## Overview

The New Tech Agent monitors AI news and generates technology implementation proposals. It helps identify emerging technologies that could benefit ZenSynora.

**Note:** This agent requires explicit user opt-in consent.

## Installation & Configuration

### Prerequisites

- Python 3.9+
- ZenSynora core installed
- Network access (for news fetching)

### Configuration

Add to your `config.py`:

```python
newtech = {
    "enabled": True,
    "interval_hours": 24,
    "share_consent": True,
    "github_repo_for_share": "https://github.com/YOUR_USERNAME/zensynora",
    "max_news_items": 10
}
```

**Important:** Set `share_consent: True` only if you want proposals shared on GitHub.

## Usage

### 1. Enable New Tech Agent

```python
from myclaw.agents.newtech_agent import enable_newtech_agent

result = enable_newtech_agent(True)
print(result)
```

**Output:**
```
✅ New Tech Agent enabled
```

### 2. Fetch AI News

```python
from myclaw.agents.newtech_agent import fetch_ai_news

result = fetch_ai_news(limit=10)
print(result)
```

**Output:**
```
📰 AI Technology News

1. **New GPT Model Released**
   Source: OpenAI
   AI technology news about: GPT, Model, Released
   Link: https://...

2. **AI Agent Framework Announcement**
   Source: Anthropic
   AI technology news about: AI, Agent, Framework
```

### 3. Summarize Technology

```python
from myclaw.agents.newtech_agent import summarize_tech

result = summarize_tech("LangChain")
print(result)
```

**Output:**
```
🔬 Technology Summary: LangChain

Category: LLM/Foundation Model
Maturity: experimental
Adoption: low
Implementation: high
Recommendation: evaluate

Technology 'LangChain' requires further research
```

### 4. Generate Proposal

```python
from myclaw.agents.newtech_agent import generate_tech_proposal

result = generate_tech_proposal("RAG Implementation")
print(result)
```

**Output:**
```
# Technology Proposal: RAG Implementation

## Summary
**Category:** Knowledge Retrieval
**Proposed:** 2026-03-29

## Implementation Plan

| Phase | Task | Effort | Risk |
|-------|------|--------|------|
| 1 | Research and evaluate | Low | Low |
| 2 | Proof of concept | Medium | Medium |
| 3 | Integration testing | High | Medium |
| 4 | Production deployment | High | High |

## Benefits
- TBD

## Risks
- Implementation complexity: high
- Adoption curve: unknown

## Recommendation
Evaluate further
```

### 5. Add to Roadmap

```python
from myclaw.agents.newtech_agent import add_to_roadmap

result = add_to_roadmap("RAG Implementation", priority="high")
print(result)
```

**Output:**
```
✅ Added 'RAG Implementation' to roadmap with high priority
```

### 6. Get Roadmap

```python
from myclaw.agents.newtech_agent import get_roadmap

result = get_roadmap()
print(result)
```

**Output:**
```
🗺️ Technology Roadmap

🔴 **RAG Implementation**
   Status: proposed
   Added: 2026-03-29T12:00:00Z

🟡 **Vector Database**
   Status: proposed
   Added: 2026-03-28T10:30:00Z
```

### 7. Get Technology Proposals

```python
from myclaw.agents.newtech_agent import get_technology_proposals

result = get_technology_proposals()
print(result)
```

**Output:**
```
📋 Technology Proposals

1. **RAG Implementation** (Knowledge Retrieval)
   Priority: high
   Status: proposed
```

### 8. Run On-Demand Scan

```python
from myclaw.agents.newtech_agent import run_newtech_scan

result = run_newtech_scan()
print(result)
```

**Output:**
```
✅ New Tech scan complete
Found 15 news items

• New GPT Model Released (OpenAI)
• AI Agent Framework Announcement (Anthropic)
• Vector Database Updates (Pinecone)
```

### 9. Share Proposal to GitHub

```python
from myclaw.agents.newtech_agent import share_proposal

result = share_proposal(
    title="RAG Implementation Proposal",
    content="# Technology Proposal...",
    format="gist"
)
print(result)
```

**Output:**
```
✅ Shared as gist: RAG Implementation Proposal
Note: Gist creation requires GitHub token configuration
```

## API Reference

### NewTechAgent Class

```python
class NewTechAgent:
    def __init__(self, enabled: bool = False, interval_hours: int = 24, share_consent: bool = False)
    def check_consent() -> bool
    def fetch_ai_news(limit: int = 10) -> List[Dict]
    def summarize_technology(tech_name: str) -> Dict
    def generate_proposal(tech_data: Dict) -> str
    def add_to_roadmap(technology: str, priority: str = "medium") -> Dict
    async share_to_github(content: str, title: str, format: str = "gist") -> Dict
    def run_ondemand() -> Dict
    def get_proposals() -> List[Dict]
    def save_proposal(proposal: Dict) -> None
```

### Tool Functions

| Function | Description |
|----------|-------------|
| `fetch_ai_news()` | Fetch AI news from sources |
| `get_technology_proposals()` | Get all proposals |
| `add_to_roadmap()` | Add technology to roadmap |
| `enable_newtech_agent()` | Enable/disable agent |
| `run_newtech_scan()` | Run on-demand scan |
| `summarize_tech()` | Create tech summary |
| `generate_tech_proposal()` | Generate implementation proposal |
| `share_proposal()` | Share proposal to GitHub |
| `get_roadmap()` | Get technology roadmap |

## Data Flow

```
AI News Sources
      │
      ▼
┌─────────────────┐
│  fetch_ai_news()│  ← Parse HTML, extract items
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│summarize_tech() │  ← Generate summary
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│generate_proposal│  ← Create implementation plan
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
save     share_to_github()
         (if consent)
```

## Troubleshooting

### Error: New Tech Agent not available

**Cause:** Agent not enabled or consent not granted.

**Solution:** Enable with `enable_newtech_agent(True)` and ensure `share_consent` is set in config.

### Error: No AI news available

**Cause:** Network issue or all sources failed.

**Solution:** Check internet connection. Results may be cached.

### Error: Share failed

**Cause:** GitHub token not configured.

**Solution:** Configure GitHub token in environment variables.

## Best Practices

1. **Start with summaries** - Use `summarize_tech()` before full proposals
2. **Prioritize roadmap** - Add high-priority items to roadmap
3. **Review proposals** - Regularly review generated proposals
4. **Consent for sharing** - Only enable share_consent if you want public sharing
5. **Configure GitHub token** - Set token in config for real sharing

## GitHub Integration

### Configuration

Add to your `config.json`:

```json
{
  "newtech": {
    "enabled": true,
    "share_consent": true,
    "github_token": "ghp_xxxxxxxxxxxx",
    "github_repo_for_share": "yourusername/zensynora"
  }
}
```

Or via environment variable:
```bash
export MYCLAW_GITHUB_TOKEN="ghp_xxxxxxxxxxxx"
```

### Sharing to GitHub

```python
from myclaw.agents.newtech_agent import share_proposal

# Share as Gist
result = share_proposal(
    title="My Proposal",
    content="# Technology Proposal...",
    format="gist"
)

# Share as Issue
result = share_proposal(
    title="Feature Request",
    content="## Description...",
    format="issue"
)
```

---

*Generated: 2026-03-29*
*Part of: ZenSynora Phase 3 + Future Implementations*
