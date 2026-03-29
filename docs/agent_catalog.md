# ZenSynora Agent System Documentation

**Version:** 1.0
**Date:** 2026-03-29
**Total Agents:** 136+

---

## Overview

The ZenSynora Agent System provides a comprehensive registry of 136+ specialized AI agents modeled after the VoltAgent Codex subagents. These agents are organized into 10 categories covering development, infrastructure, quality, data/AI, and business domains.

## Quick Start

```python
from myclaw.agents import (
    AGENT_REGISTRY,
    get_agent,
    list_agents,
    AgentCategory,
)

# Get a specific agent
agent = get_agent("backend-developer")

# List all agents in a category
backend_agents = list_agents(category=AgentCategory.CORE_DEVELOPMENT)

# Search agents by query
security_agents = list_agents(query="security")

# Get all agents
all_agents = list(AGENT_REGISTRY.values())
```

---

## Agent Categories

### 01. Core Development (12 agents)

Essential development agents for everyday coding tasks.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `api-designer` | REST and GraphQL API architect | API, Backend |
| `backend-developer` | Server-side expert for scalable APIs | Backend, API |
| `code-mapper` | Code path mapping and ownership analysis | Fullstack |
| `electron-pro` | Desktop application expert | Frontend, Mobile |
| `frontend-developer` | UI/UX specialist for React, Vue, Angular | Frontend |
| `fullstack-developer` | End-to-end feature development | Fullstack |
| `graphql-architect` | GraphQL schema and federation expert | API, Backend |
| `microservices-architect` | Distributed systems designer | Backend, API |
| `mobile-developer` | Cross-platform mobile specialist | Mobile |
| `ui-designer` | Visual design and interaction specialist | Frontend |
| `ui-fixer` | Smallest safe patch for UI issues | Frontend |
| `websocket-engineer` | Real-time communication specialist | Backend, API |

### 02. Language Specialists (27 agents)

Language-specific experts with deep framework knowledge.

| Agent | Language/Framework | Capabilities |
|-------|-------------------|--------------|
| `angular-architect` | Angular 15+ | Frontend |
| `cpp-pro` | C++ | Systems |
| `csharp-developer` | C# / .NET | Backend |
| `django-developer` | Django 4+ | Python, Backend |
| `dotnet-core-expert` | .NET 8 | C#, Backend |
| `dotnet-framework-4.8-expert` | .NET Framework | Legacy |
| `elixir-expert` | Elixir/OTP | Fault-tolerant |
| `erlang-expert` | Erlang/OTP | Distributed |
| `flutter-expert` | Flutter 3+ | Mobile |
| `golang-pro` | Go | Concurrency |
| `java-architect` | Java Enterprise | Architecture |
| `javascript-pro` | JavaScript/Node.js | Fullstack |
| `kotlin-specialist` | Kotlin/JVM | Mobile |
| `laravel-specialist` | Laravel 10+ | PHP Backend |
| `nextjs-developer` | Next.js 14+ | React Fullstack |
| `php-pro` | PHP | Backend |
| `powershell-5.1-expert` | PowerShell 5.1 | Windows Automation |
| `powershell-7-expert` | PowerShell 7+ | Cross-platform |
| `python-pro` | Python | Data, AI, Web |
| `rails-expert` | Rails 8.1 | Ruby Backend |
| `react-specialist` | React 18+ | Frontend |
| `rust-engineer` | Rust | Systems |
| `spring-boot-engineer` | Spring Boot 3+ | Java Microservices |
| `sql-pro` | SQL | Database |
| `swift-expert` | Swift/iOS | Apple |
| `typescript-pro` | TypeScript | Type-safe JS |
| `vue-expert` | Vue 3 | Frontend |

### 03. Infrastructure (16 agents)

DevOps, cloud, and deployment specialists.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `azure-infra-engineer` | Azure infrastructure | Cloud, DevOps |
| `cloud-architect` | Multi-cloud architecture | AWS/GCP/Azure |
| `database-administrator` | DB management | DBA, Backup |
| `deployment-engineer` | Deployment automation | CI/CD, GitOps |
| `devops-engineer` | CI/CD and automation | DevOps |
| `devops-incident-responder` | Incident management | On-call |
| `docker-expert` | Containerization | Docker |
| `incident-responder` | System incident response | Recovery |
| `kubernetes-specialist` | K8s orchestration | Containers |
| `network-engineer` | Network infrastructure | DNS, VPN |
| `platform-engineer` | Internal developer platforms | IDP |
| `security-engineer` | Infrastructure security | Hardening |
| `sre-engineer` | Site reliability | SLO/SLA |
| `terraform-engineer` | Terraform IaC | AWS/GCP/Azure |
| `terragrunt-expert` | Terragrunt orchestration | DRY IaC |
| `windows-infra-admin` | Active Directory | Windows |

### 04. Quality & Security (16 agents)

Testing, security, and code quality experts.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `accessibility-tester` | A11y compliance | WCAG, ARIA |
| `ad-security-reviewer` | AD security audit | IAM |
| `architect-reviewer` | Architecture review | System Design |
| `browser-debugger` | Client-side debugging | DevTools |
| `chaos-engineer` | Resilience testing | Failure Injection |
| `code-reviewer` | Code quality guardian | PR Review |
| `compliance-auditor` | Regulatory compliance | SOC2, HIPAA |
| `debugger` | Advanced debugging | Root Cause |
| `error-detective` | Error analysis | Stack Traces |
| `penetration-tester` | Ethical hacking | OWASP |
| `performance-engineer` | Performance optimization | Profiling |
| `powershell-security-hardening` | PowerShell security | Compliance |
| `qa-expert` | Test automation | QA |
| `reviewer` | PR-style review | Security |
| `security-auditor` | Vulnerability assessment | Security |
| `test-automator` | Test frameworks | Selenium |

### 05. Data & AI (12 agents)

Data engineering, ML, and AI specialists.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `ai-engineer` | AI system design | ML Systems |
| `data-analyst` | Data insights | Visualization |
| `data-engineer` | Pipeline architect | ETL, Spark |
| `data-scientist` | Analytics expert | Statistics |
| `database-optimizer` | Query optimization | Performance |
| `llm-architect` | LLM system design | RAG, Fine-tuning |
| `machine-learning-engineer` | ML engineering | TensorFlow, PyTorch |
| `ml-engineer` | ML specialist | Features |
| `mlops-engineer` | MLOps | Deployment |
| `nlp-engineer` | NLP expert | Transformers |
| `postgres-pro` | PostgreSQL expert | SQL |
| `prompt-engineer` | Prompt optimization | LLM |

### 06. Developer Experience (13 agents)

Tooling and developer productivity experts.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `build-engineer` | Build systems | Bazel, Make |
| `cli-developer` | CLI tools | argparse, Cobra |
| `dependency-manager` | Package management | npm, pip |
| `documentation-engineer` | Tech docs | Sphinx |
| `dx-optimizer` | Developer experience | Productivity |
| `git-workflow-manager` | Git strategies | Branching |
| `legacy-modernizer` | Tech debt | Refactoring |
| `mcp-developer` | MCP specialist | AI Integration |
| `powershell-module-architect` | PS modules | Gallery |
| `powershell-ui-architect` | PowerShell UI | WinForms, WPF |
| `refactoring-specialist` | Code refactoring | Patterns |
| `slack-expert` | Slack integration | Bolt |
| `tooling-engineer` | Developer tools | Linters |

### 07. Specialized Domains (12 agents)

Domain-specific technology experts.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `api-documenter` | API docs | OpenAPI |
| `blockchain-developer` | Web3, smart contracts | Solidity |
| `embedded-systems` | Embedded/RTOS | Bare-metal |
| `fintech-engineer` | Financial tech | Payments |
| `game-developer` | Unity/Unreal/Godot | Gamedev |
| `iot-engineer` | IoT systems | MQTT |
| `m365-admin` | Microsoft 365 | SharePoint |
| `mobile-app-developer` | iOS/Android apps | React Native |
| `payment-integration` | Stripe/PayPal | PCI-DSS |
| `quant-analyst` | Quantitative analysis | Trading |
| `risk-manager` | Risk assessment | Compliance |
| `seo-specialist` | Search optimization | Analytics |

### 08. Business & Product (11 agents)

Product management and business analysis.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `business-analyst` | Requirements | BPMN |
| `content-marketer` | Content strategy | SEO |
| `customer-success-manager` | CS specialist | Onboarding |
| `legal-advisor` | Legal/compliance | Contracts |
| `product-manager` | Product strategy | Roadmap |
| `project-manager` | Project delivery | PMP |
| `sales-engineer` | Technical sales | Demos |
| `scrum-master` | Agile/Scrum | Ceremonies |
| `technical-writer` | Tech writing | Manuals |
| `ux-researcher` | User research | Personas |
| `wordpress-master` | WordPress | WooCommerce |

### 09. Meta & Orchestration (12 agents)

Agent coordination and meta-programming.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `agent-installer` | Agent installation | Registry |
| `agent-organizer` | Multi-agent coordination | Workflows |
| `context-manager` | Context optimization | Tokens |
| `error-coordinator` | Error handling | Recovery |
| `it-ops-orchestrator` | IT automation | Runbooks |
| `knowledge-synthesizer` | Knowledge aggregation | Synthesis |
| `multi-agent-coordinator` | Advanced orchestration | Swarms |
| `performance-monitor` | Performance tracking | Metrics |
| `pied-piper` | Workflow automation | Pipelines |
| `task-distributor` | Task allocation | Scheduling |
| `workflow-orchestrator` | Workflow automation | DAGs |

### 10. Research & Analysis (7 agents)

Research, search, and analysis specialists.

| Agent | Description | Capabilities |
|-------|-------------|--------------|
| `competitive-analyst` | Competitive intelligence | Market |
| `data-researcher` | Data discovery | Datasets |
| `docs-researcher` | API verification | Reference |
| `market-researcher` | Market analysis | Consumer |
| `research-analyst` | Research synthesis | Methodology |
| `search-specialist` | Information retrieval | Vector search |
| `trend-analyst` | Emerging trends | Forecasting |

---

## Usage Examples

### Finding the Right Agent

```python
from myclaw.agents import list_agents, AgentCategory, AgentCapability

# Find all security-related agents
security_agents = list_agents(
    capability=AgentCapability.SECURITY
)

# Find Python developers
python_agents = list_agents(
    capability=AgentCapability.PYTHON
)

# Find agents that can help with APIs
api_agents = list_agents(query="api")

# Get all frontend agents
frontend_agents = list_agents(category=AgentCategory.CORE_DEVELOPMENT)
```

### Integration with Swarm System

```python
from myclaw.agents import get_agent
from myclaw.swarm import SwarmConfig, SwarmStrategy

# Get agent definition
agent_def = get_agent("backend-developer")

# Create a specialized swarm
swarm_config = SwarmConfig(
    name="api-development-team",
    strategy=SwarmStrategy.HIERARCHICAL,
    workers=["api-designer", "backend-developer", "code-reviewer"],
    coordinator="fullstack-developer"
)
```

### Agent Profile Loading

```python
from pathlib import Path

# Agent profiles are stored in myclaw/agent_profiles/
profile_path = Path(__file__).parent / "agent_profiles"
backend_profile = profile_path / "core-development" / "backend-developer.md"
```

---

## Model Routing

Agents use smart model routing based on task complexity:

| Model | Use Case | Examples |
|-------|----------|----------|
| `gpt-5.4` | Deep reasoning, architecture, security | security-auditor, llm-architect |
| `gpt-5.3-codex-spark` | Fast tasks, synthesis, research | search-specialist, docs-researcher |

## Sandbox Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| `read-only` | Analyze without modifying | reviewers, auditors |
| `workspace-write` | Create and modify files | developers, engineers |

---

## Extending the Registry

To add a new agent:

1. Create the agent definition in `myclaw/agents/registry.py`
2. Add a profile markdown in `myclaw/agent_profiles/{category}/`
3. Register in the appropriate `AgentCategory`

```python
# In registry.py
"my-new-agent": AgentDefinition(
    name="my-new-agent",
    description="Description of what it does",
    category=AgentCategory.CORE_DEVELOPMENT,
    capabilities={AgentCapability.BACKEND},
    profile_name="core-development/my-new-agent",
    tags={"relevant", "tags"},
)
```

---

## Agent Count Summary

| Category | Count |
|----------|-------|
| Core Development | 12 |
| Language Specialists | 27 |
| Infrastructure | 16 |
| Quality & Security | 16 |
| Data & AI | 12 |
| Developer Experience | 13 |
| Specialized Domains | 12 |
| Business & Product | 11 |
| Meta & Orchestration | 12 |
| Research & Analysis | 7 |
| **Total** | **136** |

---

## References

- Based on [VoltAgent Codex Subagents](https://github.com/VoltAgent/awesome-codex-subagents)
- Inspired by OpenAI's agent frameworks
