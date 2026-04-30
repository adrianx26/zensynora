"""New Tech Agent - AI news monitoring and technology proposal generation."""

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
from ..async_utils import run_async

logger = logging.getLogger(__name__)

NEWTECH_DIR = Path.home() / ".myclaw" / "newtech"
NEWTECH_DIR.mkdir(parents=True, exist_ok=True)
ROADMAP_FILE = NEWTECH_DIR / "roadmap.json"
PROPOSALS_FILE = NEWTECH_DIR / "proposals.json"
NEWS_CACHE = NEWTECH_DIR / "news_cache.json"

GITHUB_API_URL = "https://api.github.com"

config = None


def set_config(cfg):
    """Set global config reference for New Tech Agent."""
    global config
    config = cfg


class NewTechAgent:
    """Agent for monitoring AI news and generating technology proposals."""

    def __init__(self, enabled: bool = False, interval_hours: int = 24, share_consent: bool = False, github_token: str = ""):
        if config and hasattr(config, 'newtech'):
            self.enabled = config.newtech.enabled if hasattr(config.newtech, 'enabled') else enabled
            self.interval_hours = config.newtech.interval_hours if hasattr(config.newtech, 'interval_hours') else interval_hours
            self.share_consent = config.newtech.share_consent if hasattr(config.newtech, 'share_consent') else share_consent
            if hasattr(config.newtech, 'github_token') and config.newtech.github_token:
                token_val = config.newtech.github_token
                if hasattr(token_val, "get_secret_value"):
                    self.github_token = token_val.get_secret_value()
                else:
                    self.github_token = str(token_val)
            else:
                self.github_token = github_token
            self.max_news_items = config.newtech.max_news_items if hasattr(config.newtech, 'max_news_items') else 10
            self.github_repo_owner = getattr(config.newtech, "github_repo_owner", "") or ""
            self.github_repo_name = getattr(config.newtech, "github_repo_name", "") or ""
        else:
            self.enabled = enabled
            self.interval_hours = interval_hours
            self.share_consent = share_consent
            self.github_token = github_token
            self.max_news_items = 10
            self.github_repo_owner = ""
            self.github_repo_name = ""
        
        self.newtech_dir = NEWTECH_DIR
        self.newtech_dir.mkdir(parents=True, exist_ok=True)
        self.roadmap_file = ROADMAP_FILE
        self.proposals_file = PROPOSALS_FILE
        self.news_cache = NEWS_CACHE

    def _get_github_headers(self) -> Dict[str, str]:
        """Get GitHub API headers with authentication."""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"
        }
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def check_consent(self) -> bool:
        """Check if user has given consent for New Tech features."""
        if config and hasattr(config, 'newtech'):
            return config.newtech.enabled and config.newtech.share_consent
        return self.enabled and self.share_consent

    def fetch_ai_news(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch AI news from various sources.
        
        Args:
            limit: Maximum number of news items to fetch
            
        Returns:
            List of news item dicts
        """
        news_items = []
        
        sources = [
            ("Hugging Face", "https://huggingface.co/blog"),
            ("AI News", "https://www.artificialintelligence-news.com/"),
            ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/"),
        ]
        
        for name, url in sources:
            try:
                import requests
                headers = {'User-Agent': 'Mozilla/5.0'}
                response = requests.get(url, headers=headers, timeout=10)
                if response.status_code == 200:
                    items = self._parse_news_items(response.text, name)
                    news_items.extend(items)
            except Exception as e:
                logger.debug(f"Could not fetch from {name}: {e}")
        
        if not news_items and self.news_cache.exists():
            try:
                cached = json.loads(self.news_cache.read_text())
                if datetime.now().timestamp() - cached.get("timestamp", 0) < 3600:
                    news_items = cached.get("items", [])
            except Exception:
                pass
        
        return news_items[:limit]

    def _parse_news_items(self, html: str, source: str) -> List[Dict[str, Any]]:
        """Parse news items from HTML content."""
        items = []
        
        title_pattern = re.compile(r'<h[12][^>]*>([^<]+)</h[12]>')
        link_pattern = re.compile(r'href="([^"]+)"')
        
        titles = title_pattern.findall(html)[:5]
        links = link_pattern.findall(html)[:5]
        
        for i, title in enumerate(titles):
            if title and len(title.strip()) > 10:
                items.append({
                    "title": title.strip(),
                    "source": source,
                    "url": links[i] if i < len(links) else "",
                    "timestamp": datetime.now().isoformat(),
                    "summary": self._generate_summary(title)
                })
        
        return items

    def _generate_summary(self, title: str) -> str:
        """Generate a brief summary from title."""
        keywords = re.findall(r'\b[A-Z][a-z]+|[A-Z]{2,}\b', title)
        return f"AI technology news about: {', '.join(keywords[:5])}"

    def summarize_technology(self, tech_name: str) -> Dict[str, Any]:
        """Create a 10-row summary for a technology.
        
        Args:
            tech_name: Name of the technology
            
        Returns:
            Dict with technology summary
        """
        return {
            "name": tech_name,
            "category": self._categorize_tech(tech_name),
            "maturity": "experimental",
            "adoption": "low",
            "trends": [],
            "use_cases": [],
            "alternatives": [],
            "implementation_difficulty": "high",
            "recommendation": "evaluate",
            "notes": f"Technology '{tech_name}' requires further research"
        }

    def _categorize_tech(self, tech_name: str) -> str:
        """Categorize technology based on name."""
        lower = tech_name.lower()
        if any(k in lower for k in ['llm', 'model', 'gpt', 'transformer']):
            return "LLM/Foundation Model"
        if any(k in lower for k in ['agent', 'autonomous']):
            return "AI Agent"
        if any(k in lower for k in ['rag', 'retrieval', 'vector']):
            return "Knowledge Retrieval"
        if any(k in lower for k in ['safety', 'align', 'govern']):
            return "AI Safety"
        return "General AI"

    def generate_proposal(self, tech_data: Dict[str, Any]) -> str:
        """Generate implementation proposal for a technology.
        
        Args:
            tech_data: Technology data dict
            
        Returns:
            Formatted proposal string
        """
        name = tech_data.get("name", "Unknown")
        category = tech_data.get("category", "AI Technology")
        
        proposal = f"""# Technology Proposal: {name}

## Summary
**Category:** {category}
**Proposed:** {datetime.now().strftime('%Y-%m-%d')}

## Overview
{tech_data.get('summary', 'No summary available')}

## Implementation Plan

| Phase | Task | Effort | Risk |
|-------|------|--------|------|
| 1 | Research and evaluate | Low | Low |
| 2 | Proof of concept | Medium | Medium |
| 3 | Integration testing | High | Medium |
| 4 | Production deployment | High | High |

## Benefits
- {tech_data.get('use_cases', ['TBD'])[0] if tech_data.get('use_cases') else 'To be determined'}

## Risks
- Implementation complexity: {tech_data.get('implementation_difficulty', 'Unknown')}
- Adoption curve: {tech_data.get('adoption', 'Unknown')}

## Recommendation
{tech_data.get('recommendation', 'Evaluate further')}
"""
        return proposal

    def add_to_roadmap(self, technology: str, priority: str = "medium") -> Dict[str, Any]:
        """Add a technology to the roadmap.
        
        Args:
            technology: Technology name
            priority: Priority level ('low', 'medium', 'high')
            
        Returns:
            Dict with result
        """
        roadmap = {}
        if self.roadmap_file.exists():
            try:
                roadmap = json.loads(self.roadmap_file.read_text())
            except Exception:
                pass
        
        entry_id = technology.lower().replace(' ', '_')
        roadmap[entry_id] = {
            "name": technology,
            "priority": priority,
            "added": datetime.now().isoformat(),
            "status": "proposed"
        }
        
        self.roadmap_file.write_text(json.dumps(roadmap, indent=2), encoding="utf-8")
        
        return {"success": True, "technology": technology, "priority": priority}

    async def share_to_github(self, content: str, title: str, format: str = "gist") -> Dict[str, Any]:
        """Share proposal on GitHub (opt-in).
        
        Args:
            content: Content to share
            title: Title for the share
            format: Format ('gist' or 'issue')
            
        Returns:
            Dict with share result
        """
        if not self.check_consent():
            return {"success": False, "message": "Share consent not granted"}
        
        try:
            if format == "gist":
                return await self._create_gist(title, content)
            else:
                return await self._create_issue(title, content)
        except Exception as e:
            logger.error(f"Error sharing to GitHub: {e}")
            return {"success": False, "message": str(e)}

    async def _create_gist(self, title: str, content: str) -> Dict[str, Any]:
        """Create a GitHub Gist with the content."""
        if not self.github_token:
            return {
                "success": False,
                "type": "gist",
                "title": title,
                "message": "GitHub token not configured. Set github_token in config."
            }
        
        try:
            import requests
            gist_data = {
                "description": title,
                "public": False,
                "files": {
                    f"{title.replace(' ', '_')}.md": {
                        "content": content
                    }
                }
            }
            response = requests.post(
                f"{GITHUB_API_URL}/gists",
                headers=self._get_github_headers(),
                json=gist_data,
                timeout=30
            )
            if response.status_code == 201:
                data = response.json()
                return {
                    "success": True,
                    "type": "gist",
                    "title": title,
                    "url": data.get("html_url"),
                    "gist_id": data.get("id")
                }
            else:
                return {
                    "success": False,
                    "type": "gist",
                    "message": f"Failed to create gist: {response.status_code}"
                }
        except Exception as e:
            logger.error(f"Error creating gist: {e}")
            return {"success": False, "message": str(e)}

    async def _create_issue(self, title: str, content: str) -> Dict[str, Any]:
        """Create a GitHub Issue with the content."""
        if not self.github_token:
            return {
                "success": False,
                "type": "issue",
                "title": title,
                "message": "GitHub token not configured. Set github_token in config."
            }
        if not self.github_repo_owner or not self.github_repo_name:
            return {
                "success": False,
                "type": "issue",
                "title": title,
                "message": "GitHub repository not configured. Set github_repo_owner and github_repo_name."
            }
        if "/" in self.github_repo_owner or "/" in self.github_repo_name:
            return {
                "success": False,
                "type": "issue",
                "title": title,
                "message": "Invalid repository format. Owner and repo name must not contain '/'."
            }
        
        try:
            import requests
            issue_data = {
                "title": title,
                "body": content
            }
            response = requests.post(
                f"{GITHUB_API_URL}/repos/{self.github_repo_owner}/{self.github_repo_name}/issues",
                headers=self._get_github_headers(),
                json=issue_data,
                timeout=30
            )
            if response.status_code == 201:
                data = response.json()
                return {
                    "success": True,
                    "type": "issue",
                    "title": title,
                    "url": data.get("html_url"),
                    "issue_number": data.get("number")
                }
            else:
                return {
                    "success": False,
                    "type": "issue",
                    "message": f"Failed to create issue: {response.status_code}"
                }
        except Exception as e:
            logger.error(f"Error creating issue: {e}")
            return {"success": False, "message": str(e)}

    def run_ondemand(self) -> Dict[str, Any]:
        """Run New Tech agent on-demand (bypasses interval check)."""
        if not self.check_consent():
            return {"success": False, "message": "New Tech Agent not enabled or consent not granted"}
        
        news = self.fetch_ai_news(limit=10)
        return {
            "success": True,
            "news_count": len(news),
            "news": news[:3],
            "message": "On-demand scan complete"
        }

    def get_proposals(self) -> List[Dict[str, Any]]:
        """Get all stored technology proposals."""
        if not self.proposals_file.exists():
            return []
        
        try:
            return json.loads(self.proposals_file.read_text())
        except Exception:
            return []

    def save_proposal(self, proposal: Dict[str, Any]) -> None:
        """Save a technology proposal."""
        proposals = self.get_proposals()
        proposals.append(proposal)
        self.proposals_file.write_text(json.dumps(proposals, indent=2), encoding="utf-8")


def fetch_ai_news(limit: int = 10) -> str:
    """Fetch AI news from various sources.
    
    Args:
        limit: Maximum number of news items
        
    Returns:
        Formatted news list
    """
    agent = NewTechAgent()
    news = agent.fetch_ai_news(limit)
    
    if not news:
        return "📰 No AI news available. Check your internet connection."
    
    lines = ["📰 AI Technology News", ""]
    for i, item in enumerate(news[:limit], 1):
        lines.append(f"{i}. **{item['title']}**")
        lines.append(f"   Source: {item['source']}")
        lines.append(f"   {item['summary']}")
        if item.get('url'):
            lines.append(f"   Link: {item['url']}")
        lines.append("")
    
    return "\n".join(lines)


def get_technology_proposals() -> str:
    """Get all technology proposals.
    
    Returns:
        Formatted proposal list
    """
    agent = NewTechAgent()
    proposals = agent.get_proposals()
    
    if not proposals:
        return "📋 No technology proposals yet. Use summarize_technology() to create one."
    
    lines = ["📋 Technology Proposals", ""]
    for i, prop in enumerate(proposals, 1):
        lines.append(f"{i}. **{prop.get('name', 'Unknown')}** ({prop.get('category', 'General')})")
        lines.append(f"   Priority: {prop.get('priority', 'medium')}")
        lines.append(f"   Status: {prop.get('status', 'proposed')}")
        lines.append("")
    
    return "\n".join(lines)


def add_to_roadmap(technology: str, priority: str = "medium") -> str:
    """Add a technology to the roadmap.
    
    Args:
        technology: Technology name
        priority: Priority level ('low', 'medium', 'high')
    
    Returns:
        Addition confirmation
    """
    agent = NewTechAgent()
    result = agent.add_to_roadmap(technology, priority)
    
    if result.get("success"):
        return f"✅ Added '{technology}' to roadmap with {priority} priority"
    return f"❌ Failed to add to roadmap: {result.get('message', 'Unknown error')}"


def enable_newtech_agent(enabled: bool = True) -> str:
    """Enable or disable the New Tech Agent.
    
    Args:
        enabled: True to enable, False to disable
        
    Returns:
        Status message
    """
    global config
    if config and hasattr(config, 'newtech'):
        config.newtech.enabled = enabled
        return f"✅ New Tech Agent {'enabled' if enabled else 'disabled'}"
    return f"ℹ️ New Tech Agent {'enabled' if enabled else 'disabled'} (config update pending restart)"


def run_newtech_scan() -> str:
    """Run on-demand AI news scan.
    
    Returns:
        Scan results
    """
    agent = NewTechAgent()
    result = agent.run_ondemand()
    
    if not result.get("success"):
        return f"❌ New Tech Agent not available: {result.get('message')}"
    
    lines = [f"✅ New Tech scan complete", f"Found {result.get('news_count', 0)} news items", ""]
    for item in result.get("news", [])[:3]:
        lines.append(f"• {item['title']} ({item['source']})")
    
    return "\n".join(lines)


def summarize_tech(tech_name: str) -> str:
    """Create a summary for a technology.
    
    Args:
        tech_name: Name of the technology
    
    Returns:
        Formatted summary
    """
    agent = NewTechAgent()
    summary = agent.summarize_technology(tech_name)
    
    lines = [f"🔬 Technology Summary: {tech_name}", ""]
    lines.append(f"Category: {summary['category']}")
    lines.append(f"Maturity: {summary['maturity']}")
    lines.append(f"Adoption: {summary['adoption']}")
    lines.append(f"Implementation: {summary['implementation_difficulty']}")
    lines.append(f"Recommendation: {summary['recommendation']}")
    lines.append("")
    lines.append(summary['notes'])
    
    return "\n".join(lines)


def generate_tech_proposal(tech_name: str) -> str:
    """Generate implementation proposal for a technology.
    
    Args:
        tech_name: Name of the technology
    
    Returns:
        Formatted proposal
    """
    agent = NewTechAgent()
    tech_data = agent.summarize_technology(tech_name)
    tech_data["name"] = tech_name
    proposal = agent.generate_proposal(tech_data)
    
    agent.save_proposal({
        "name": tech_name,
        "category": tech_data["category"],
        "proposal": proposal,
        "timestamp": datetime.now().isoformat()
    })
    
    return proposal


def share_proposal(title: str, content: str, format: str = "gist") -> str:
    """Share a proposal on GitHub.
    
    Args:
        title: Proposal title
        content: Proposal content
        format: Format ('gist' or 'issue')
    
    Returns:
        Share result
    """

    
    github_token = ""
    if config and hasattr(config, 'newtech'):
        github_token = getattr(config.newtech, 'github_token', "")
    
    agent = NewTechAgent(github_token=github_token)
    result = run_async(agent.share_to_github, content, title, format)
    
    if result.get("success"):
        return f"✅ Shared as {result.get('type')}: {title}\nURL: {result.get('url', 'N/A')}"
    return f"❌ Share failed: {result.get('message')}"


def get_roadmap() -> str:
    """Get the technology roadmap.
    
    Returns:
        Formatted roadmap
    """
    roadmap_file = ROADMAP_FILE
    
    if not roadmap_file.exists():
        return "📍 Roadmap is empty. Use add_to_roadmap() to add technologies."
    
    try:
        roadmap = json.loads(roadmap_file.read_text())
        
        if not roadmap:
            return "📍 Roadmap is empty."
        
        lines = ["🗺️ Technology Roadmap", ""]
        
        for entry_id, info in roadmap.items():
            priority = info.get('priority', 'medium')
            icon = "🔴" if priority == "high" else "🟡" if priority == "medium" else "🟢"
            lines.append(f"{icon} **{info.get('name', entry_id)}**")
            lines.append(f"   Status: {info.get('status', 'proposed')}")
            lines.append(f"   Added: {info.get('added', 'Unknown')}")
            lines.append("")
        
        return "\n".join(lines)
    
    except Exception as e:
        logger.error(f"Error reading roadmap: {e}")
        return f"❌ Error reading roadmap: {e}"
