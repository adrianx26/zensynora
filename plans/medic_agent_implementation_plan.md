# Enhanced MedicAgent: Agent Ecosystem Health & Integrity

This plan outlines the expansion of the **Medic Agent** to provide comprehensive lifecycle management for the 136+ specialized agents in ZenSynora. It transforms the Medic Agent from a simple file-integrity checker into a proactive system for diagnosing, repairing, and evolving the entire agent registry.

## Proposed Changes

### 1. Registry Expansion
#### [MODIFY] [registry.py](file:///f:/ANTI/zensynora/myclaw/agents/registry.py)
*   Add **`SYSTEM_INFRASTRUCTURE`** to the `AgentCategory` enum.
*   Add a new `MAINTENANCE` tag to the `AgentCapability` enum.
*   Register the **`medic-agent`** itself as a first-class agent in the `META_ORCHESTRATION` category, enabling users to address it directly (e.g., `@medic-agent status`).

### 2. Core Medic Logic Upgrades
#### [MODIFY] [medic_agent.py](file:///f:/ANTI/zensynora/myclaw/agents/medic_agent.py)
Update the `MedicAgent` class with specialized agent-aware modules:

*   **Integrity Expansion**: Modify `scan_system` to dynamically discover and hash all files in `myclaw/agent_profiles/`, ensuring the entire persona catalog is protected by SHA-256 integrity checks.
*   **`evaluate_agent(agent_name)`**:
    *   Verify cross-referencing between `registry.py` and `agent_profiles/`.
    *   Validate metadata fields (description length, tag consistency, routing validity).
    *   Assign a "Quality Score" based on instruction clarity and checkbox coverage.
*   **`diagnose_agent(agent_name)`**:
    *   Identify specific failures (e.g., "Profile file missing", "Syntax error in registry").
    *   Detect optimization opportunities (e.g., "Instructions are too short", "Outdated model routing").
*   **`repair_agent(agent_name)`**:
    *   Integrate with the existing recovery logic to automatically restore corrupted profiles from GitHub or backups.
*   **`improve_agent(agent_name)`**:
    *   Use LLM-assisted generation to suggest missing `instructions` or `checkboxes` for underdeveloped agents.
    *   Optimize `reasoning_effort` and `model_routing` based on the agent's complexity.

### 3. Documentation & Profiles
#### [NEW] [medic-agent.md](file:///f:/ANTI/zensynora/myclaw/agent_profiles/meta-orchestration/medic-agent.md)
*   Create a detailed persona for the Medic Agent, outlining its authority and technical maintenance tools.

## User Review Required

> [!IMPORTANT]
> **Automated Self-Repair**: By default, the Medic Agent will only *report* issues. I've designed it to require a manual confirmation before overwriting local changes with GitHub/backup versions during a "Repair" operation to prevent data loss.

## Verification Plan

### Automated Tests
*   Run the newly implemented `evaluate_all_agents()` and verify it detects a purposely deleted profile file.
*   Verify that `check_integrity()` correctly identifies a manually modified `registry.py`.
*   Validate the `medic-agent` registration by attempting to query it via the CLI.

### Manual Verification
*   Execute `@medic-agent status` to see the newly formatted ZenSynora Health Report.
*   Verify that the 136 agents are now correctly tracked in `integrity_registry.json`.
