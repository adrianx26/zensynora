"""
Tools — Session Insights & User Profiles
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .core import (
    WORKSPACE, TOOLBOX_DIR, TOOLBOX_REG, TOOLBOX_DOCS,
    ALLOWED_COMMANDS, BLOCKED_COMMANDS,
    _rate_limiter, _tool_audit_logger,
    _agent_registry, _job_queue, _user_chat_ids, _notification_callback,
    _runtime_config,
    TOOLS, TOOL_SCHEMAS,
    validate_path,
    get_parallel_executor,
    is_tool_independent,
)

import json
import asyncio
from pathlib import Path
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Periodic Session Reflection ─────────────────────────────────────────────

def schedule_daily_reflection(user_id: str = "default", hour: int = 20, minute: int = 0) -> str:
    """Schedule a daily session reflection that analyzes what was learned and saves to knowledge base.

    This creates a recurring task that runs at the specified time each day, analyzes
    recent conversations, and writes insights to the knowledge base with tag 'daily_reflection'.

    user_id: User ID for notification routing
    hour: Hour of day to run (0-23, default: 20 = 8 PM)
    minute: Minute of hour (0-59, default: 0)

    Returns:
        Success or error message.
    """
    if _job_queue is None and _notification_callback is None:
        return "Error: Scheduler not available (no channel gateway running)."

    task = (
        "Analyze recent conversations and write a daily reflection to knowledge base. "
        "Use write_to_knowledge with title format 'Daily Reflection YYYY-MM-DD', "
        "tags: ['daily_reflection', 'session_summary'], and content summarizing: "
        "1) Key topics discussed, "
        "2) Important decisions made, "
        "3) Tasks completed, "
        "4) User preferences observed, "
        "5) Insights gained. "
        "Format as a structured summary with sections."
    )

    # Calculate delay until next occurrence of the specified time
    from datetime import datetime, timedelta
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If target time has passed today, schedule for tomorrow
    if target <= now:
        target += timedelta(days=1)

    delay = int((target - now).total_seconds())

    # Create daily recurring job
    job_id = f"daily_reflection_{user_id}"

    # Remove existing daily reflection jobs to avoid duplicates
    existing_jobs = _job_queue.get_jobs_by_name(job_id) if _job_queue else []
    for job in existing_jobs:
        job.schedule_removal()

    return _create_job_internal(task, delay, 86400, user_id, job_id) + f" (scheduled daily at {hour:02d}:{minute:02d})"


def generate_session_insights(user_id: str = "default", save_to_knowledge: bool = True) -> str:
    """Generate insights from recent session conversations and persist them to the knowledge base.

    Reads the user's last 50 messages, asks the LLM to identify key topics, preferences,
    important facts, and action items, then optionally saves the result as a dated KB entry
    tagged 'session-insights'.

    user_id: User ID for memory access and knowledge base isolation.
    save_to_knowledge: Whether to write insights to the knowledge base (default: True).

    Returns:
        Formatted insight summary (and confirmation if saved to KB).
    """
    from .memory import Memory
    from .config import load_config
    from .provider import get_provider
    from datetime import datetime
    import asyncio

    try:
        # --- Step 1: Load recent conversation history ---
        async def _load_history():
            mem = Memory(user_id=user_id)
            await mem.initialize()
            h = await mem.get_history(limit=50)
            await mem.close()
            return h

        try:
            loop = asyncio.new_event_loop()
            history = loop.run_until_complete(_load_history())
            loop.close()
        except Exception as e:
            logger.error(f"Could not load history: {e}")
            return f"Error loading conversation history: {e}"

        if not history:
            return "No conversation history available for analysis."

        # --- Step 2: Build the analysis prompt ---
        analysis_lines = [
            "Analyse the following recent conversation and produce a structured insight report.",
            "Include: (1) Key topics and themes, (2) Important facts or decisions made,",
            "(3) User preferences or patterns observed, (4) Actionable items or follow-ups.",
            "Be concise and specific. Use bullet points.\n",
        ]
        for m in history[-20:]:  # Focus on last 20 messages for relevance
            role = m.get("role", "unknown")
            content = m.get("content", "")[:250]
            analysis_lines.append(f"{role}: {content}")
        analysis_prompt = "\n".join(analysis_lines)

        # --- Step 3: Call the LLM ---
        async def _call_llm():
            config = load_config()
            provider = get_provider(config)
            model = getattr(config.agents.defaults, "model", "llama3.2")
            insights, _ = await provider.chat(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an insightful conversation analyst. "
                            "Extract and summarise key learnings from the conversation in a clear, structured way."
                        ),
                    },
                    {"role": "user", "content": analysis_prompt},
                ],
                model=model,
            )
            return insights

        try:
            loop2 = asyncio.new_event_loop()
            insights_text = loop2.run_until_complete(_call_llm())
            loop2.close()
        except Exception as e:
            logger.error(f"LLM call failed in generate_session_insights: {e}")
            return f"Error generating insights from LLM: {e}"

        if not insights_text or not insights_text.strip():
            return "The LLM returned an empty analysis. Please try again."

        # --- Step 4: Save to knowledge base ---
        if save_to_knowledge:
            date_str = datetime.now().strftime("%Y-%m-%d")
            kb_result = write_to_knowledge(
                title=f"Session Insights {date_str}",
                content=insights_text,
                tags="session-insights,auto-generated,daily-reflection",
                user_id=user_id,
            )
            return f"✅ Session insights saved to knowledge base ({kb_result}).\n\n{insights_text}"

        return insights_text

    except Exception as e:
        logger.error(f"Error generating session insights: {e}")
        return f"Error generating insights: {e}"



def extract_user_preferences(user_id: str = "default") -> str:
    """Analyze conversation history to extract user preferences and style.

    Builds a profile of the user's communication style, preferences, interests,
    and patterns that can be used to personalize future interactions.

    user_id: User ID for memory access

    Returns:
        JSON string with user profile data, or error message.
    """
    from .memory import Memory
    import asyncio

    try:
        mem = Memory(user_id=user_id)
        asyncio.get_event_loop().run_until_complete(mem.initialize())
        history = asyncio.get_event_loop().run_until_complete(mem.get_history(limit=100))

        if len(history) < 5:
            return "Not enough conversation history to build profile. Need at least 5 messages."

        # Analyze for patterns
        user_messages = [m['content'] for m in history if m['role'] == 'user']

        # Extract keywords and topics
        all_text = ' '.join(user_messages).lower()

        # Simple pattern analysis
        preferences = {
            "total_conversations": len([m for m in history if m['role'] == 'user']),
            "avg_message_length": sum(len(m) for m in user_messages) // max(1, len(user_messages)),
            "topics_mentioned": [],
            "questions_asked": sum(1 for m in user_messages if '?' in m),
            "commands_used": sum(1 for m in user_messages if any(c in m.lower() for c in ['calculate', 'search', 'find', 'get', 'show', 'list', 'create', 'make'])),
        }

        # Look for topic patterns (simplified)
        topic_keywords = {
            'coding': ['code', 'python', 'function', 'debug', 'programming', 'script'],
            'data': ['data', 'database', 'query', 'sql', 'table'],
            'files': ['file', 'read', 'write', 'open', 'save', 'folder'],
            'research': ['research', 'search', 'find', 'look up', 'information'],
            'tasks': ['task', 'schedule', 'remind', 'todo', 'plan'],
            'creative': ['write', 'story', 'creative', 'explain', 'describe'],
        }

        for topic, keywords in topic_keywords.items():
            if any(kw in all_text for kw in keywords):
                preferences['topics_mentioned'].append(topic)

        import json
        profile_json = json.dumps(preferences, indent=2)

        # Optionally save to knowledge base
        try:
            tags_str = ",".join(["user_profile", "preferences", "auto-extracted"])
            permalink = write_to_knowledge(
                title=f"User Profile - {user_id}",
                content=profile_json,
                tags=tags_str,
                user_id=user_id
            )
            return f"Profile saved to knowledge base.\n\n{profile_json}"
        except:
            return profile_json

    except Exception as e:
        logger.error(f"Error extracting user preferences: {e}")
        return f"Error extracting preferences: {e}"


def update_user_profile(insights: str, user_id: str = "default") -> str:
    """Update the user dialectic profile with new insights.

    Writes insights to the user dialectic profile file that the agent
    can read on startup to customize responses.

    insights: Markdown content to add to the profile
    user_id: User ID (used for knowledge base fallback)

    Returns:
        Success or error message.
    """
    from pathlib import Path

    dialectic_path = Path(__file__).parent / "profiles" / "user_dialectic.md"

    try:
        dialectic_path.parent.mkdir(parents=True, exist_ok=True)

        # Read existing content
        existing = dialectic_path.read_text() if dialectic_path.exists() else ""

        # Add insights section with timestamp
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        new_section = f"\n\n## Insights ({timestamp})\n{insights}"

        # Check if section already exists and update it
        if "## Insights" in existing:
            existing = existing.split("## Insights")[0] + new_section
        else:
            existing += new_section

        dialectic_path.write_text(existing, encoding="utf-8")

        # Also save to knowledge base as backup
        try:
            tags_str = ",".join(["user_profile", "dialectic", "manual_update"])
            write_to_knowledge(
                title=f"User Profile Update - {timestamp}",
                content=insights,
                tags=tags_str,
                user_id=user_id
            )
        except:
            pass

        return f"✅ User dialectic profile updated at {timestamp}"

    except Exception as e:
        logger.error(f"Error updating user profile: {e}")
        return f"Error updating profile: {e}"


def get_user_profile(user_id: str = "default") -> str:
    """Get the current user dialectic profile.

    Reads the user dialectic profile file and returns its contents.

    user_id: User ID (for consistency, not used for file lookup)

    Returns:
        User profile content or placeholder message.
    """
    from pathlib import Path

    dialectic_path = Path(__file__).parent / "profiles" / "user_dialectic.md"

    try:
        if dialectic_path.exists():
            return dialectic_path.read_text(encoding="utf-8")
        else:
            return "No user dialectic profile found. Use extract_user_preferences() to generate one."
    except Exception as e:
        logger.error(f"Error reading user profile: {e}")
        return f"Error reading profile: {e}"

