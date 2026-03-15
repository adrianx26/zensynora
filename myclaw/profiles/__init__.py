"""Agent profiles for MyClaw.

This directory contains markdown profile files that define agent behavior and personalities.
Each profile is loaded based on the agent name.

Profile Loading Order:
1. Local workspace: myclaw/profiles/{agent_name}.md
2. User home: ~/.myclaw/profiles/{agent_name}.md
3. Default system prompt (built-in)

Available Profiles:
- default.md: Default agent with all capabilities
- agent.md: Core agent capabilities reference
- soul.md: Ethical guidelines and principles
- identity.md: Agent personality and communication style
- user.md: User preferences template
- heartbeat.md: System monitoring and health checks
- bootstrap.md: Initialization and startup sequence
- memory.md: Memory management guidelines
"""
