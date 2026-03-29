# ZenSynora (MyClaw) Implementation Plan

## Overview
Based on comparison with Hermes Agent and inspiration from OpenClaw, this plan outlines improvements across 4 phases.

---

## PHASE 1: Quick Wins (8 hours) ✅ COMPLETED

### 1.1 Plugin Lifecycle Hooks (~2 hours) ✅
**File:** `myclaw/tools.py`

Add hooks system for extensibility:
- `HOOKS = {'pre_llm_call': [], 'post_llm_call': [], 'on_session_start': [], 'on_session_end': []}`
- `register_hook(event_type, callback)` function
- `trigger_hook(event_type, data)` function
- Call hooks from `agent.py` think() method before/after LLM calls

### 1.2 Trajectory Compression Enhancement (~1 hour) ✅
**File:** `myclaw/agent.py`

Improve existing summarization (line 234+):
- Store compressed summaries in knowledge base with tag `session_summary`
- Add `compression_ratio` metric
- Include important decisions/actions in summary

### 1.3 Natural Language Scheduling (~3 hours) ✅
**File:** `myclaw/tools.py`

Add `_parse_natural_schedule()` function supporting:
- "at 8 AM daily"
- "every Monday at 9pm"
- "every 2 hours"
- `nlp_schedule(task, natural_time)` tool

### 1.4 Enhanced Cross-Session Recall (~2 hours) ✅
**File:** `myclaw/memory.py`

Improve FTS5 queries in `search()` method:
- Better query construction
- Cache recent successful retrievals

---

## PHASE 2: Skill System Evolution (12 hours) ✅ COMPLETED

### 2.1 Skill Metadata Structure (~2 hours) ✅
**File:** `myclaw/tools.py`

Updated `TOOLBOX_REG` schema:
- name, version, description, tags, author, created, last_modified, eval_score, eval_count, enabled

### 2.2 Skill Evaluation Harness (~4 hours) ✅
**File:** `myclaw/tools.py`

Added functions:
- `get_skill_info(skill_name)` - detailed skill info
- `enable_skill(skill_name)` - enable disabled skill
- `disable_skill(skill_name)` - soft delete (keeps file, removes from execution)
- `update_skill_metadata()` - update tags, description, version
- `benchmark_skill(skill_name, test_cases)` - run test cases
- `evaluate_skill(skill_name)` - basic sanity checks

### 2.3 Skill Self-Improvement (~6 hours) ✅
**File:** `myclaw/tools.py`

- `improve_skill(name, improved_code)` - replace skill with safety checks
- Version increment on update (patch)
- Automatic backup before changes
- Rollback capability with `rollback_skill(name)`
- Requires: docstring, error handling, logger.error()

---

## PHASE 3: Memory & Learning (7 hours) ✅ COMPLETED

### 3.1 Periodic Session Reflection (~3 hours) ✅
**File:** `myclaw/tools.py`

Added functions:
- `schedule_daily_reflection(user_id, hour, minute)` - schedules daily analysis
- `generate_session_insights(user_id)` - analyzes recent conversations
- `extract_user_preferences(user_id)` - extracts user patterns

### 3.2 User Dialectic Profile (~4 hours) ✅
**New File:** `myclaw/profiles/user_dialectic.md`
**File:** `myclaw/agent.py` (updated)
**File:** `myclaw/tools.py` (updated)

- Created `user_dialectic.md` template
- Agent loads profile on startup and appends to system prompt
- Added tools: `update_user_profile()`, `get_user_profile()`

---

## PHASE 4: ZenHub Ecosystem (8 hours) ✅ COMPLETED

### 4.1 ZenHub Local Registry (~5 hours) ✅
**New File:** `myclaw/hub/__init__.py`

Created ZenHub module with functions:
- `hub_search(query)` - search skills by name/description/tags
- `hub_list()` - list all published skills
- `hub_publish(skill_name)` - publish from TOOLBOX to ZenHub
- `hub_install(skill_name)` - install from ZenHub to TOOLBOX
- `hub_remove(skill_name)` - remove from ZenHub
- `hub_search` / `hub_list` / `hub_publish` / `hub_install` / `hub_remove`

### 4.2 External Skill Directory Support (~3 hours) ✅
**File:** `myclaw/hub/__init__.py`

Added functions:
- `discover_external_skills()` - scan ~/.myclaw/skills/ directory
- `hub_install_from_external(skill_name)` - import external skills

---

## ✅ ALL PHASES COMPLETED

## Summary of Implemented Features

### Phase 1: Quick Wins
- Plugin lifecycle hooks (pre_llm_call, post_llm_call, on_session_start, on_session_end)
- Improved trajectory compression with compression ratio logging
- Natural language scheduling (at 8 AM daily, every Monday at 9pm, etc.)
- Enhanced FTS5 cross-session recall with BM25 scoring and recency boosting

### Phase 2: Skill System Evolution
- Full skill metadata (version, tags, author, eval_score, eval_count, enabled)
- Skill evaluation harness (benchmark_skill, evaluate_skill)
- Auto-disable for low-scoring skills
- Skill self-improvement (improve_skill, rollback_skill)
- Safety checks: AST validation, syntax check, docstring/logger requirements

### Phase 3: Memory & Learning
- Periodic session reflection (schedule_daily_reflection)
- Session insights generation
- User dialectic profile (user_dialectic.md template)
- Agent loads profile on startup for personalization
- User preference extraction from conversation history

### Phase 4: ZenHub Ecosystem
- Local skill registry (~/.myclaw/hub/)
- Skill publishing and installation
- External skill directory auto-discovery
- Download counting and skill popularity tracking

---

## New Tools Added (Total: ~35 new functions)

| Category | Tools |
|----------|-------|
| Lifecycle Hooks | register_hook, list_hooks, clear_hooks |
| Scheduling | nlp_schedule |
| Skill Management | get_skill_info, enable_skill, disable_skill, update_skill_metadata, benchmark_skill, evaluate_skill, improve_skill, rollback_skill |
| Session Learning | schedule_daily_reflection, generate_session_insights, extract_user_preferences, update_user_profile, get_user_profile |
| ZenHub | hub_search, hub_list, hub_publish, hub_install, hub_remove, discover_external_skills, hub_install_from_external |

---

## Category Summary

### Self-Improvement & Learning Loop
- Closed Learning Loop
- Autonomous Skill Creation
- Skill Self-Improvement
- Periodic Nudges / Session Reflection
- Trajectory Analysis & Compression

### Skill & Tool System
- Evolve TOOLBOX to full Skill System
- Skill Evaluation Harness
- ZenHub (local/public registry)
- External Skill Directories
- Plugin Lifecycle Hooks

### Memory & Knowledge Management
- LLM-powered Summarization
- Cross-Session Recall
- User Modeling / Dialectic Profile
- Knowledge Graph Integration

### Scheduling & Automation
- Cron Daemon
- Natural Language Scheduling
- Background Task Delivery
- Heartbeat Monitoring

### Agent Architecture
- Sub-agent Delegation via RPC
- Unified State Management (ZenState)
- Multi-Agent Coordination

### User Experience
- Natural Language Scheduling
- Voice Memo Transcription (optional)
- Better Streaming & Autocomplete
- Session Resume

### Ecosystem
- ZenHub Registry
- External Skill Compatibility
- Documentation