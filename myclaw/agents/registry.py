"""
Agent Registry - Central registry for all specialized Codex-style agents.

This module provides a comprehensive registry of 136+ specialized agents
organized across 10 categories for various development and business tasks.

Usage:
    from myclaw.agents.registry import AGENT_REGISTRY, get_agent, list_agents

    # Get a specific agent
    agent = get_agent("backend-developer")

    # List all agents in a category
    agents = list_agents(category="core-development")

    # List agents by capability
    agents = list_agents(tags=["security", "review"])
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set
import logging

logger = logging.getLogger(__name__)


class AgentCategory(Enum):
    """Agent category classifications."""
    CORE_DEVELOPMENT = "core-development"
    LANGUAGE_SPECIALISTS = "language-specialists"
    INFRASTRUCTURE = "infrastructure"
    QUALITY_SECURITY = "quality-security"
    DATA_AI = "data-ai"
    DEVELOPER_EXPERIENCE = "developer-experience"
    SPECIALIZED_DOMAINS = "specialized-domains"
    BUSINESS_PRODUCT = "business-product"
    META_ORCHESTRATION = "meta-orchestration"
    RESEARCH_ANALYSIS = "research-analysis"


class AgentCapability(Enum):
    """Agent capability tags."""
    # Development
    BACKEND = "backend"
    FRONTEND = "frontend"
    FULLSTACK = "fullstack"
    MOBILE = "mobile"
    API = "api"
    DATABASE = "database"

    # Languages
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    JAVA = "java"
    GO = "go"
    RUST = "rust"
    CPP = "cpp"
    CSHARP = "csharp"
    PHP = "php"
    RUBY = "ruby"
    SWIFT = "swift"
    KOTLIN = "kotlin"

    # Infrastructure
    DEVOPS = "devops"
    CLOUD = "cloud"
    KUBERNETES = "kubernetes"
    DOCKER = "docker"
    TERRAFORM = "terraform"
    SECURITY = "security"
    SRE = "sre"

    # Quality
    TESTING = "testing"
    REVIEW = "review"
    DEBUGGING = "debugging"
    PERFORMANCE = "performance"
    ACCESSIBILITY = "accessibility"

    # Data/AI
    AI = "ai"
    ML = "ml"
    MLOPS = "mlops"
    DATA = "data"
    NLP = "nlp"

    # Meta
    ORCHESTRATION = "orchestration"
    COORDINATION = "coordination"
    RESEARCH = "research"
    DOCUMENTATION = "documentation"


@dataclass
class AgentDefinition:
    """Definition of a specialized agent."""
    name: str
    description: str
    category: AgentCategory
    capabilities: Set[AgentCapability]
    profile_name: str
    model_routing: str = "gpt-5.3-codex-spark"
    reasoning_effort: str = "medium"
    sandbox_mode: str = "read-only"
    tags: Set[str] = field(default_factory=set)
    instructions: str = ""
    checkboxes: List[str] = field(default_factory=list)

    def matches_query(self, query: str) -> bool:
        """Check if agent matches a search query."""
        q = query.lower()
        return (
            q in self.name.lower() or
            q in self.description.lower() or
            any(q in cap.value.lower() for cap in self.capabilities) or
            any(q in tag.lower() for tag in self.tags)
        )


# Profile directory
AGENT_PROFILES_DIR = Path(__file__).parent.parent / "agent_profiles"


def _load_profile(profile_name: str) -> str:
    """Load agent profile content."""
    profile_path = AGENT_PROFILES_DIR / f"{profile_name}.md"
    if profile_path.exists():
        return profile_path.read_text(encoding="utf-8")
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# YAML data-file loader (Sprint 12)
# ─────────────────────────────────────────────────────────────────────────────
#
# Adding a new specialized agent should be a *data PR*, not a code PR.
# This loader reads ``myclaw/agents/data/agents.yaml`` — the canonical
# source of truth — and produces ``AgentDefinition`` instances.
#
# The embedded Python-literal registry below ALSO remains, on purpose:
#
# 1. It serves as the fallback when the YAML file is missing or
#    malformed, so a corrupted data file can't make the framework
#    unbootable mid-deploy.
# 2. It documents the canonical record shape (mypy + IDE autocomplete
#    work better against the literal than against arbitrary YAML).
# 3. The two stay synchronized via ``tests/test_registry_yaml.py``,
#    which fails CI if the YAML and the literal disagree.
#
# When the literal is finally removed, this module shrinks from ~70KB
# to ~5KB. That cleanup is intentionally deferred until the YAML format
# has been stable across at least one minor release.

AGENT_DATA_FILE = Path(__file__).parent / "data" / "agents.yaml"


def _parse_yaml_record(record: Dict) -> Optional[AgentDefinition]:
    """Convert one YAML record into an :class:`AgentDefinition`.

    Returns ``None`` and logs a warning when the record is malformed —
    individual bad rows don't take down the whole registry.
    """
    try:
        cat_str = record["category"]
        category = AgentCategory(cat_str)
    except (KeyError, ValueError):
        logger.warning(
            "Skipping agent record with unknown/missing category: %r",
            record.get("name", "<unnamed>"),
        )
        return None

    capabilities: Set[AgentCapability] = set()
    for cap_str in record.get("capabilities", []):
        try:
            capabilities.add(AgentCapability(cap_str))
        except ValueError:
            logger.warning(
                "Agent %r references unknown capability %r — skipping that capability",
                record.get("name"), cap_str,
            )

    try:
        return AgentDefinition(
            name=record["name"],
            description=record["description"],
            category=category,
            capabilities=capabilities,
            profile_name=record["profile_name"],
            model_routing=record.get("model_routing", "gpt-5.3-codex-spark"),
            reasoning_effort=record.get("reasoning_effort", "medium"),
            sandbox_mode=record.get("sandbox_mode", "read-only"),
            tags=set(record.get("tags", [])),
            instructions=record.get("instructions", ""),
            checkboxes=list(record.get("checkboxes", [])),
        )
    except KeyError as e:
        logger.warning(
            "Agent record missing required field %s: %r", e, record.get("name", "<unnamed>"),
        )
        return None


def load_agents_from_yaml(path: Optional[Path] = None) -> Dict[str, AgentDefinition]:
    """Load the agent catalog from a YAML file.

    Returns ``{}`` when the file is missing, malformed, or PyYAML isn't
    installed — the caller should fall back to the embedded literal in
    that case so the framework still boots.
    """
    target = path or AGENT_DATA_FILE
    if not target.exists():
        return {}
    try:
        import yaml  # PyYAML is a core dep already
    except ImportError:
        logger.warning("PyYAML not installed; skipping agents.yaml load")
        return {}
    try:
        records = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Failed to parse %s: %s", target, e)
        return {}
    if not isinstance(records, list):
        logger.warning("%s: top-level value must be a list; got %s", target, type(records))
        return {}

    out: Dict[str, AgentDefinition] = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        agent = _parse_yaml_record(r)
        if agent is None:
            continue
        if agent.name in out:
            logger.warning("Duplicate agent name in YAML: %r — keeping first", agent.name)
            continue
        out[agent.name] = agent
    return out


# ─────────────────────────────────────────────────────────────────────────────
# AGENT REGISTRY - 136+ Specialized Agents (embedded fallback)
# ─────────────────────────────────────────────────────────────────────────────

_LITERAL_AGENT_REGISTRY: Dict[str, AgentDefinition] = {

    # ══════════════════════════════════════════════════════════════════════════
    # 01. CORE DEVELOPMENT - 12 agents
    # ══════════════════════════════════════════════════════════════════════════

    "api-designer": AgentDefinition(
        name="api-designer",
        description="REST and GraphQL API architect - designs scalable API schemas and contracts",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.API, AgentCapability.BACKEND},
        profile_name="core-development/api-designer",
        tags={"api", "rest", "graphql", "schema", "openapi"},
        instructions="You are an API design specialist...",
        checkboxes=[
            "Use OpenAPI/Swagger for REST API definitions",
            "Design GraphQL schemas with proper typing",
            "Consider rate limiting and pagination",
            "Document error responses",
        ]
    ),

    "backend-developer": AgentDefinition(
        name="backend-developer",
        description="Server-side expert for scalable APIs and distributed systems",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.BACKEND, AgentCapability.API},
        profile_name="core-development/backend-developer",
        tags={"backend", "server", "api", "microservices", "scalability"},
        instructions="You are a backend development specialist...",
        checkboxes=[
            "Implement proper error handling",
            "Use appropriate caching strategies",
            "Consider transaction patterns",
            "Ensure observability with logging/metrics",
        ]
    ),

    "code-mapper": AgentDefinition(
        name="code-mapper",
        description="Code path mapping and ownership boundary analysis",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="core-development/code-mapper",
        tags={"architecture", "code-analysis", "ownership", "dependencies"},
        instructions="You analyze code ownership and dependencies...",
        checkboxes=[
            "Identify module boundaries",
            "Map dependency graphs",
            "Find circular dependencies",
            "Document code ownership",
        ]
    ),

    "electron-pro": AgentDefinition(
        name="electron-pro",
        description="Desktop application expert for Electron framework",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.FRONTEND, AgentCapability.MOBILE},
        profile_name="core-development/electron-pro",
        tags={"electron", "desktop", "cross-platform", "node.js"},
        instructions="You specialize in Electron desktop applications...",
        checkboxes=[
            "Use context isolation properly",
            "Implement IPC securely",
            "Handle native modules correctly",
            "Optimize for performance",
        ]
    ),

    "frontend-developer": AgentDefinition(
        name="frontend-developer",
        description="UI/UX specialist for React, Vue, and Angular frameworks",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.FRONTEND},
        profile_name="core-development/frontend-developer",
        tags={"frontend", "react", "vue", "angular", "ui", "ux"},
        instructions="You are a frontend development specialist...",
        checkboxes=[
            "Follow component best practices",
            "Implement proper state management",
            "Ensure accessibility standards",
            "Optimize bundle size",
        ]
    ),

    "fullstack-developer": AgentDefinition(
        name="fullstack-developer",
        description="End-to-end feature development across full stack",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.FRONTEND, AgentCapability.BACKEND, AgentCapability.FULLSTACK},
        profile_name="core-development/fullstack-developer",
        tags={"fullstack", "end-to-end", "frontend", "backend", "database"},
        instructions="You are a full-stack development specialist...",
        checkboxes=[
            "Design database schemas",
            "Implement API endpoints",
            "Build responsive UI components",
            "Handle deployment",
        ]
    ),

    "graphql-architect": AgentDefinition(
        name="graphql-architect",
        description="GraphQL schema and federation expert",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.API, AgentCapability.BACKEND},
        profile_name="core-development/graphql-architect",
        tags={"graphql", "federation", "schema", "api", "subgraph"},
        instructions="You specialize in GraphQL architecture...",
        checkboxes=[
            "Design efficient schemas",
            "Implement proper resolvers",
            "Use DataLoader for N+1",
            "Plan for schema evolution",
        ]
    ),

    "microservices-architect": AgentDefinition(
        name="microservices-architect",
        description="Distributed systems designer for microservices architecture",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.BACKEND, AgentCapability.API},
        profile_name="core-development/microservices-architect",
        tags={"microservices", "distributed-systems", "architecture", "services"},
        instructions="You design microservices architectures...",
        checkboxes=[
            "Define service boundaries",
            "Plan inter-service communication",
            "Implement circuit breakers",
            "Consider eventual consistency",
        ]
    ),

    "mobile-developer": AgentDefinition(
        name="mobile-developer",
        description="Cross-platform mobile specialist (iOS, Android, React Native, Flutter)",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.MOBILE},
        profile_name="core-development/mobile-developer",
        tags={"mobile", "ios", "android", "react-native", "flutter", "cross-platform"},
        instructions="You specialize in mobile development...",
        checkboxes=[
            "Follow platform guidelines",
            "Handle offline gracefully",
            "Implement push notifications",
            "Optimize for performance",
        ]
    ),

    "ui-designer": AgentDefinition(
        name="ui-designer",
        description="Visual design and interaction specialist",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.FRONTEND},
        profile_name="core-development/ui-designer",
        tags={"ui", "design", "ux", "visual", "interaction", "components"},
        instructions="You design user interfaces...",
        checkboxes=[
            "Create consistent design systems",
            "Ensure visual hierarchy",
            "Design for accessibility",
            "Prototype interactions",
        ]
    ),

    "ui-fixer": AgentDefinition(
        name="ui-fixer",
        description="Smallest safe patch for reproduced UI issues",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.FRONTEND},
        profile_name="core-development/ui-fixer",
        tags={"ui", "fix", "bug", "patch", "css", "styling"},
        instructions="You fix UI bugs with minimal changes...",
        checkboxes=[
            "Reproduce the bug first",
            "Make smallest fix possible",
            "Test across browsers/devices",
            "Avoid breaking other UIs",
        ]
    ),

    "websocket-engineer": AgentDefinition(
        name="websocket-engineer",
        description="Real-time communication specialist for WebSocket connections",
        category=AgentCategory.CORE_DEVELOPMENT,
        capabilities={AgentCapability.BACKEND, AgentCapability.API},
        profile_name="core-development/websocket-engineer",
        tags={"websocket", "real-time", "socket", "bi-directional", "push"},
        instructions="You specialize in WebSocket communications...",
        checkboxes=[
            "Handle connection lifecycle",
            "Implement reconnection logic",
            "Consider security implications",
            "Use appropriate protocols",
        ]
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 02. LANGUAGE SPECIALISTS - 27 agents
    # ══════════════════════════════════════════════════════════════════════════

    "angular-architect": AgentDefinition(
        name="angular-architect",
        description="Angular 15+ enterprise patterns expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.FRONTEND},
        profile_name="language-specialists/angular-architect",
        tags={"angular", "typescript", "rxjs", "enterprise", "spa"},
        model_routing="gpt-5.4",
        reasoning_effort="high",
    ),

    "cpp-pro": AgentDefinition(
        name="cpp-pro",
        description="C++ performance expert for systems programming",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.CPP},
        profile_name="language-specialists/cpp-pro",
        tags={"c++", "performance", "systems", "memory", "templates"},
        model_routing="gpt-5.4",
    ),

    "csharp-developer": AgentDefinition(
        name="csharp-developer",
        description=".NET ecosystem specialist for C# development",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.CSHARP},
        profile_name="language-specialists/csharp-developer",
        tags={"csharp", "dotnet", "net", "asp.net", "winforms", "wpf"},
    ),

    "django-developer": AgentDefinition(
        name="django-developer",
        description="Django 4+ web development expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.PYTHON, AgentCapability.BACKEND},
        profile_name="language-specialists/django-developer",
        tags={"django", "python", "web", "orm", "rest"},
    ),

    "dotnet-core-expert": AgentDefinition(
        name="dotnet-core-expert",
        description=".NET 8 cross-platform development specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.CSHARP, AgentCapability.BACKEND},
        profile_name="language-specialists/dotnet-core-expert",
        tags={"dotnet", "dotnet-core", "asp.net-core", "blazor", "cross-platform"},
    ),

    "dotnet-framework-4.8-expert": AgentDefinition(
        name="dotnet-framework-4.8-expert",
        description=".NET Framework legacy enterprise specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.CSHARP},
        profile_name="language-specialists/dotnet-framework-4.8-expert",
        tags={"dotnet-framework", "legacy", "enterprise", "vb.net", "winforms"},
        model_routing="gpt-5.4",
    ),

    "elixir-expert": AgentDefinition(
        name="elixir-expert",
        description="Elixir and OTP fault-tolerant systems expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.BACKEND},
        profile_name="language-specialists/elixir-expert",
        tags={"elixir", "otp", "functional", "fault-tolerant", "beam"},
        model_routing="gpt-5.4",
    ),

    "erlang-expert": AgentDefinition(
        name="erlang-expert",
        description="Erlang/OTP and rebar3 engineering expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.BACKEND},
        profile_name="language-specialists/erlang-expert",
        tags={"erlang", "otp", "rebar3", "telecom", "distributed"},
        model_routing="gpt-5.4",
    ),

    "flutter-expert": AgentDefinition(
        name="flutter-expert",
        description="Flutter 3+ cross-platform mobile expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.MOBILE, AgentCapability.FRONTEND},
        profile_name="language-specialists/flutter-expert",
        tags={"flutter", "dart", "mobile", "cross-platform", "widget"},
    ),

    "golang-pro": AgentDefinition(
        name="golang-pro",
        description="Go concurrency specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.GO, AgentCapability.BACKEND},
        profile_name="language-specialists/golang-pro",
        tags={"go", "golang", "concurrency", "goroutines", "microservices"},
    ),

    "java-architect": AgentDefinition(
        name="java-architect",
        description="Enterprise Java architect for large-scale systems",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.JAVA, AgentCapability.BACKEND},
        profile_name="language-specialists/java-architect",
        tags={"java", "spring", "enterprise", "architecture", "jvm"},
        model_routing="gpt-5.4",
    ),

    "javascript-pro": AgentDefinition(
        name="javascript-pro",
        description="JavaScript development expert for modern JS ecosystems",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.JAVASCRIPT, AgentCapability.FRONTEND},
        profile_name="language-specialists/javascript-pro",
        tags={"javascript", "node.js", "npm", "es6", "frontend", "backend"},
    ),

    "kotlin-specialist": AgentDefinition(
        name="kotlin-specialist",
        description="Modern JVM language expert for Android and server-side Kotlin",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.KOTLIN, AgentCapability.MOBILE},
        profile_name="language-specialists/kotlin-specialist",
        tags={"kotlin", "jvm", "android", "spring", "coroutines"},
    ),

    "laravel-specialist": AgentDefinition(
        name="laravel-specialist",
        description="Laravel 10+ PHP framework expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.BACKEND},
        profile_name="language-specialists/laravel-specialist",
        tags={"laravel", "php", "blade", "eloquent", "api"},
    ),

    "nextjs-developer": AgentDefinition(
        name="nextjs-developer",
        description="Next.js 14+ full-stack React specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.FRONTEND, AgentCapability.BACKEND},
        profile_name="language-specialists/nextjs-developer",
        tags={"nextjs", "react", "ssr", "ssg", "api-routes", "typescript"},
    ),

    "php-pro": AgentDefinition(
        name="php-pro",
        description="PHP web development expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.BACKEND},
        profile_name="language-specialists/php-pro",
        tags={"php", "laravel", "symfony", "wordpress", "web"},
    ),

    "powershell-5.1-expert": AgentDefinition(
        name="powershell-5.1-expert",
        description="Windows PowerShell 5.1 and .NET Framework automation specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.BACKEND},
        profile_name="language-specialists/powershell-5.1-expert",
        tags={"powershell", "windows", "automation", "active-directory", "exchange"},
    ),

    "powershell-7-expert": AgentDefinition(
        name="powershell-7-expert",
        description="Cross-platform PowerShell 7+ automation specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.BACKEND},
        profile_name="language-specialists/powershell-7-expert",
        tags={"powershell", "pwsh", "cross-platform", "automation", "core"},
    ),

    "python-pro": AgentDefinition(
        name="python-pro",
        description="Python ecosystem master for data, web, and AI",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.PYTHON, AgentCapability.BACKEND, AgentCapability.AI},
        profile_name="language-specialists/python-pro",
        tags={"python", "data", "ai", "web", "automation", "scripting"},
    ),

    "rails-expert": AgentDefinition(
        name="rails-expert",
        description="Rails 8.1 rapid development expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.BACKEND},
        profile_name="language-specialists/rails-expert",
        tags={"ruby", "rails", "ruby-on-rails", "activerecord", "api"},
    ),

    "react-specialist": AgentDefinition(
        name="react-specialist",
        description="React 18+ modern patterns specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.FRONTEND, AgentCapability.JAVASCRIPT},
        profile_name="language-specialists/react-specialist",
        tags={"react", "hooks", "context", "redux", "server-components"},
    ),

    "rust-engineer": AgentDefinition(
        name="rust-engineer",
        description="Systems programming expert for Rust",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.RUST, AgentCapability.BACKEND},
        profile_name="language-specialists/rust-engineer",
        tags={"rust", "systems", "performance", "memory-safety", "concurrency"},
        model_routing="gpt-5.4",
    ),

    "spring-boot-engineer": AgentDefinition(
        name="spring-boot-engineer",
        description="Spring Boot 3+ microservices expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.JAVA, AgentCapability.BACKEND},
        profile_name="language-specialists/spring-boot-engineer",
        tags={"spring-boot", "java", "microservices", "spring-cloud", "jpa"},
    ),

    "sql-pro": AgentDefinition(
        name="sql-pro",
        description="Database query optimization and SQL expert",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.DATABASE, AgentCapability.BACKEND},
        profile_name="language-specialists/sql-pro",
        tags={"sql", "postgresql", "mysql", "query-optimization", "database"},
    ),

    "swift-expert": AgentDefinition(
        name="swift-expert",
        description="iOS and macOS native development specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.SWIFT, AgentCapability.MOBILE},
        profile_name="language-specialists/swift-expert",
        tags={"swift", "ios", "macos", "swiftui", "uikit", "apple"},
    ),

    "typescript-pro": AgentDefinition(
        name="typescript-pro",
        description="TypeScript specialist for type-safe JavaScript",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.TYPESCRIPT, AgentCapability.FRONTEND},
        profile_name="language-specialists/typescript-pro",
        tags={"typescript", "types", "javascript", "node.js", "react"},
    ),

    "vue-expert": AgentDefinition(
        name="vue-expert",
        description="Vue 3 Composition API specialist",
        category=AgentCategory.LANGUAGE_SPECIALISTS,
        capabilities={AgentCapability.FRONTEND, AgentCapability.JAVASCRIPT},
        profile_name="language-specialists/vue-expert",
        tags={"vue", "vue3", "composition-api", "pinia", "nuxt"},
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 03. INFRASTRUCTURE - 16 agents
    # ══════════════════════════════════════════════════════════════════════════

    "azure-infra-engineer": AgentDefinition(
        name="azure-infra-engineer",
        description="Azure infrastructure and Az PowerShell automation expert",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.CLOUD, AgentCapability.DEVOPS},
        profile_name="infrastructure/azure-infra-engineer",
        tags={"azure", "cloud", "arm-templates", "azcli", "powershell"},
    ),

    "cloud-architect": AgentDefinition(
        name="cloud-architect",
        description="Multi-cloud architecture specialist (AWS/GCP/Azure)",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.CLOUD, AgentCapability.DEVOPS},
        profile_name="infrastructure/cloud-architect",
        tags={"aws", "gcp", "azure", "multi-cloud", "architecture", "serverless"},
        model_routing="gpt-5.4",
        reasoning_effort="high",
    ),

    "database-administrator": AgentDefinition(
        name="database-administrator",
        description="Database management, backup, and recovery expert",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.DATABASE, AgentCapability.SRE},
        profile_name="infrastructure/database-administrator",
        tags={"database", "dba", "backup", "recovery", "replication", "sharding"},
    ),

    "deployment-engineer": AgentDefinition(
        name="deployment-engineer",
        description="Deployment automation and CI/CD pipeline specialist",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.DEVOPS},
        profile_name="infrastructure/deployment-engineer",
        tags={"deployment", "ci-cd", "automation", "release-management", "gitops"},
    ),

    "devops-engineer": AgentDefinition(
        name="devops-engineer",
        description="CI/CD and infrastructure automation expert",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.DEVOPS, AgentCapability.CLOUD},
        profile_name="infrastructure/devops-engineer",
        tags={"devops", "ci-cd", "jenkins", "github-actions", "gitlab", "automation"},
    ),

    "devops-incident-responder": AgentDefinition(
        name="devops-incident-responder",
        description="DevOps incident management and response specialist",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.DEVOPS, AgentCapability.SRE},
        profile_name="infrastructure/devops-incident-responder",
        tags={"incident", "response", "on-call", "postmortem", "blameless"},
    ),

    "docker-expert": AgentDefinition(
        name="docker-expert",
        description="Docker containerization and optimization specialist",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.DOCKER, AgentCapability.DEVOPS},
        profile_name="infrastructure/docker-expert",
        tags={"docker", "container", "dockerfile", "docker-compose", "containerization"},
    ),

    "incident-responder": AgentDefinition(
        name="incident-responder",
        description="System incident response and recovery expert",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.SRE, AgentCapability.DEVOPS},
        profile_name="infrastructure/incident-responder",
        tags={"incident", "response", "recovery", "monitoring", "alerting"},
    ),

    "kubernetes-specialist": AgentDefinition(
        name="kubernetes-specialist",
        description="Container orchestration master for Kubernetes",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.KUBERNETES, AgentCapability.DOCKER},
        profile_name="infrastructure/kubernetes-specialist",
        tags={"kubernetes", "k8s", "helm", "ingress", "service-mesh", "pods"},
        model_routing="gpt-5.4",
    ),

    "network-engineer": AgentDefinition(
        name="network-engineer",
        description="Network infrastructure and security specialist",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.CLOUD, AgentCapability.SECURITY},
        profile_name="infrastructure/network-engineer",
        tags={"network", "dns", "tcp-ip", "vpn", "load-balancing", "subnetting"},
    ),

    "platform-engineer": AgentDefinition(
        name="platform-engineer",
        description="Internal developer platform architecture specialist",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.DEVOPS, AgentCapability.CLOUD},
        profile_name="infrastructure/platform-engineer",
        tags={"platform", "idp", "developer-experience", "golden-path", "self-service"},
        model_routing="gpt-5.4",
    ),

    "security-engineer": AgentDefinition(
        name="security-engineer",
        description="Infrastructure security and hardening specialist",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.SECURITY, AgentCapability.DEVOPS},
        profile_name="infrastructure/security-engineer",
        tags={"security", "hardening", "iam", "encryption", "compliance"},
        model_routing="gpt-5.4",
    ),

    "sre-engineer": AgentDefinition(
        name="sre-engineer",
        description="Site reliability engineering expert",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.SRE, AgentCapability.DEVOPS},
        profile_name="infrastructure/sre-engineer",
        tags={"sre", "reliability", "slo", "sla", "monitoring", "on-call"},
    ),

    "terraform-engineer": AgentDefinition(
        name="terraform-engineer",
        description="Infrastructure as Code using Terraform expert",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.TERRAFORM, AgentCapability.CLOUD},
        profile_name="infrastructure/terraform-engineer",
        tags={"terraform", "iac", "hcl", "aws", "azure", "gcp", "modules"},
    ),

    "terragrunt-expert": AgentDefinition(
        name="terragrunt-expert",
        description="Terragrunt orchestration and DRY IaC specialist",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.TERRAFORM, AgentCapability.CLOUD},
        profile_name="infrastructure/terragrunt-expert",
        tags={"terragrunt", "terraform", "iac", "dry", "multi-account"},
    ),

    "windows-infra-admin": AgentDefinition(
        name="windows-infra-admin",
        description="Active Directory, DNS, DHCP, and GPO automation specialist",
        category=AgentCategory.INFRASTRUCTURE,
        capabilities={AgentCapability.DEVOPS, AgentCapability.SECURITY},
        profile_name="infrastructure/windows-infra-admin",
        tags={"windows", "active-directory", "dns", "dhcp", "gpo", "automation"},
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 04. QUALITY & SECURITY - 16 agents
    # ══════════════════════════════════════════════════════════════════════════

    "accessibility-tester": AgentDefinition(
        name="accessibility-tester",
        description="A11y compliance WCAG 2.1 expert",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.ACCESSIBILITY, AgentCapability.TESTING},
        profile_name="quality-security/accessibility-tester",
        tags={"accessibility", "wcag", "a11y", "aria", "screen-reader", "compliance"},
    ),

    "ad-security-reviewer": AgentDefinition(
        name="ad-security-reviewer",
        description="Active Directory security and GPO audit specialist",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.SECURITY},
        profile_name="quality-security/ad-security-reviewer",
        tags={"active-directory", "security", "gpo", "audit", "iam", "ldap"},
        model_routing="gpt-5.4",
    ),

    "architect-reviewer": AgentDefinition(
        name="architect-reviewer",
        description="Architecture review specialist for system design",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="quality-security/architect-reviewer",
        tags={"architecture", "review", "design", "patterns", "scalability"},
        model_routing="gpt-5.4",
        reasoning_effort="high",
    ),

    "browser-debugger": AgentDefinition(
        name="browser-debugger",
        description="Browser-based reproduction and client-side debugging",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.DEBUGGING, AgentCapability.FRONTEND},
        profile_name="quality-security/browser-debugger",
        tags={"browser", "debug", "chrome", "firefox", "devtools", "reproduction"},
    ),

    "chaos-engineer": AgentDefinition(
        name="chaos-engineer",
        description="System resilience testing expert for chaos engineering",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.TESTING, AgentCapability.SRE},
        profile_name="quality-security/chaos-engineer",
        tags={"chaos", "resilience", "testing", "failure-injection", "litmus", "gremlin"},
    ),

    "code-reviewer": AgentDefinition(
        name="code-reviewer",
        description="Code quality guardian for pull requests",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.REVIEW, AgentCapability.FULLSTACK},
        profile_name="quality-security/code-reviewer",
        tags={"code-review", "quality", "best-practices", "style", "linting"},
    ),

    "compliance-auditor": AgentDefinition(
        name="compliance-auditor",
        description="Regulatory compliance expert (SOC2, HIPAA, GDPR, PCI)",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.SECURITY},
        profile_name="quality-security/compliance-auditor",
        tags={"compliance", "soc2", "hipaa", "gdpr", "pci", "audit", "regulatory"},
        model_routing="gpt-5.4",
        reasoning_effort="high",
    ),

    "debugger": AgentDefinition(
        name="debugger",
        description="Advanced debugging specialist for complex issues",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.DEBUGGING},
        profile_name="quality-security/debugger",
        tags={"debugging", "troubleshooting", "root-cause", "investigation"},
    ),

    "error-detective": AgentDefinition(
        name="error-detective",
        description="Error analysis and resolution expert",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.DEBUGGING, AgentCapability.REVIEW},
        profile_name="quality-security/error-detective",
        tags={"error", "exception", "stack-trace", "analysis", "resolution"},
    ),

    "penetration-tester": AgentDefinition(
        name="penetration-tester",
        description="Ethical hacking and penetration testing specialist",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.SECURITY},
        profile_name="quality-security/penetration-tester",
        tags={"penetration-testing", "ethical-hacking", "security", "vulnerability", "owasp"},
        model_routing="gpt-5.4",
        sandbox_mode="read-only",
    ),

    "performance-engineer": AgentDefinition(
        name="performance-engineer",
        description="Performance optimization and profiling expert",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.PERFORMANCE, AgentCapability.TESTING},
        profile_name="quality-security/performance-engineer",
        tags={"performance", "profiling", "optimization", "latency", "throughput"},
        model_routing="gpt-5.4",
    ),

    "powershell-security-hardening": AgentDefinition(
        name="powershell-security-hardening",
        description="PowerShell security hardening and compliance specialist",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.SECURITY},
        profile_name="quality-security/powershell-security-hardening",
        tags={"powershell", "security", "hardening", "compliance", "script-analysis"},
    ),

    "qa-expert": AgentDefinition(
        name="qa-expert",
        description="Test automation and QA methodology specialist",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.TESTING},
        profile_name="quality-security/qa-expert",
        tags={"qa", "testing", "automation", "quality", "test-plan"},
    ),

    "reviewer": AgentDefinition(
        name="reviewer",
        description="PR-style review for correctness, security, and regressions",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.REVIEW, AgentCapability.SECURITY},
        profile_name="quality-security/reviewer",
        tags={"review", "pr", "security", "correctness", "regression"},
    ),

    "security-auditor": AgentDefinition(
        name="security-auditor",
        description="Security vulnerability assessment expert",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.SECURITY},
        profile_name="quality-security/security-auditor",
        tags={"security", "audit", "vulnerability", "assessment", "pentest"},
        model_routing="gpt-5.4",
        sandbox_mode="read-only",
    ),

    "test-automator": AgentDefinition(
        name="test-automator",
        description="Test automation framework development specialist",
        category=AgentCategory.QUALITY_SECURITY,
        capabilities={AgentCapability.TESTING},
        profile_name="quality-security/test-automator",
        tags={"test-automation", "selenium", "playwright", "cypress", "framework"},
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 05. DATA & AI - 12 agents
    # ══════════════════════════════════════════════════════════════════════════

    "ai-engineer": AgentDefinition(
        name="ai-engineer",
        description="AI system design and deployment specialist",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.AI},
        profile_name="data-ai/ai-engineer",
        tags={"ai", "machine-learning", "deployment", "mlops", "inference"},
        model_routing="gpt-5.4",
    ),

    "data-analyst": AgentDefinition(
        name="data-analyst",
        description="Data insights and visualization specialist",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.DATA},
        profile_name="data-ai/data-analyst",
        tags={"data-analysis", "visualization", "pandas", "matplotlib", "tableau"},
    ),

    "data-engineer": AgentDefinition(
        name="data-engineer",
        description="Data pipeline architect for ETL and streaming",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.DATA},
        profile_name="data-ai/data-engineer",
        tags={"data-engineering", "etl", "pipeline", "spark", "airflow", "kafka"},
        model_routing="gpt-5.4",
    ),

    "data-scientist": AgentDefinition(
        name="data-scientist",
        description="Analytics and statistical insights expert",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.DATA, AgentCapability.ML},
        profile_name="data-ai/data-scientist",
        tags={"data-science", "statistics", "python", "r", "analytics", "建模"},
        model_routing="gpt-5.4",
    ),

    "database-optimizer": AgentDefinition(
        name="database-optimizer",
        description="Database performance and query optimization specialist",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.DATABASE},
        profile_name="data-ai/database-optimizer",
        tags={"database", "optimization", "query", "indexing", "performance"},
    ),

    "llm-architect": AgentDefinition(
        name="llm-architect",
        description="Large language model system architect",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.AI},
        profile_name="data-ai/llm-architect",
        tags={"llm", "gpt", "bert", "transformer", "fine-tuning", "rag", "prompt-engineering"},
        model_routing="gpt-5.4",
        reasoning_effort="high",
    ),

    "machine-learning-engineer": AgentDefinition(
        name="machine-learning-engineer",
        description="ML systems development and deployment expert",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.ML, AgentCapability.AI},
        profile_name="data-ai/machine-learning-engineer",
        tags={"machine-learning", "ml", "tensorflow", "pytorch", "training", "inference"},
        model_routing="gpt-5.4",
    ),

    "ml-engineer": AgentDefinition(
        name="ml-engineer",
        description="Machine learning engineering specialist",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.ML},
        profile_name="data-ai/ml-engineer",
        tags={"ml", "machine-learning", "engineering", "production", "features"},
    ),

    "mlops-engineer": AgentDefinition(
        name="mlops-engineer",
        description="MLOps and model deployment specialist",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.MLOPS, AgentCapability.AI},
        profile_name="data-ai/mlops-engineer",
        tags={"mlops", "model-registry", "feature-store", "deployment", "monitoring"},
        model_routing="gpt-5.4",
    ),

    "nlp-engineer": AgentDefinition(
        name="nlp-engineer",
        description="Natural language processing expert",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.NLP, AgentCapability.AI},
        profile_name="data-ai/nlp-engineer",
        tags={"nlp", "text", "transformers", "bert", "gpt", "sentiment", "ner"},
        model_routing="gpt-5.4",
    ),

    "postgres-pro": AgentDefinition(
        name="postgres-pro",
        description="PostgreSQL database expert",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.DATABASE},
        profile_name="data-ai/postgres-pro",
        tags={"postgresql", "postgres", "database", "sql", "performance", "tuning"},
    ),

    "prompt-engineer": AgentDefinition(
        name="prompt-engineer",
        description="Prompt optimization and engineering specialist",
        category=AgentCategory.DATA_AI,
        capabilities={AgentCapability.AI},
        profile_name="data-ai/prompt-engineer",
        tags={"prompt-engineering", "llm", "gpt", "optimization", "few-shot", "chain-of-thought"},
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 06. DEVELOPER EXPERIENCE - 13 agents
    # ══════════════════════════════════════════════════════════════════════════

    "build-engineer": AgentDefinition(
        name="build-engineer",
        description="Build system specialist for complex projects",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/build-engineer",
        tags={"build", "compilation", "make", "cmake", "bazel", "turborepo"},
    ),

    "cli-developer": AgentDefinition(
        name="cli-developer",
        description="Command-line tool creator specialist",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/cli-developer",
        tags={"cli", "command-line", "argparse", "cobra", "commander", "tooling"},
    ),

    "dependency-manager": AgentDefinition(
        name="dependency-manager",
        description="Package and dependency management specialist",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/dependency-manager",
        tags={"dependencies", "npm", "pip", "cargo", "packages", "versioning", "semver"},
    ),

    "documentation-engineer": AgentDefinition(
        name="documentation-engineer",
        description="Technical documentation expert",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.DOCUMENTATION},
        profile_name="developer-experience/documentation-engineer",
        tags={"documentation", "docs", "markdown", "openapi", "sphinx", "docusaurus"},
    ),

    "dx-optimizer": AgentDefinition(
        name="dx-optimizer",
        description="Developer experience optimization specialist",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/dx-optimizer",
        tags={"developer-experience", "dx", "productivity", "tooling", "automation"},
        model_routing="gpt-5.4",
    ),

    "git-workflow-manager": AgentDefinition(
        name="git-workflow-manager",
        description="Git workflow and branching strategy expert",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/git-workflow-manager",
        tags={"git", "branching", "workflow", "github-flow", "gitflow", "rebase"},
    ),

    "legacy-modernizer": AgentDefinition(
        name="legacy-modernizer",
        description="Legacy code modernization specialist",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/legacy-modernizer",
        tags={"legacy", "modernization", "refactoring", "tech-debt", "migration"},
        model_routing="gpt-5.4",
    ),

    "mcp-developer": AgentDefinition(
        name="mcp-developer",
        description="Model Context Protocol specialist",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.AI},
        profile_name="developer-experience/mcp-developer",
        tags={"mcp", "model-context-protocol", "ai", "tooling", "integration"},
    ),

    "powershell-module-architect": AgentDefinition(
        name="powershell-module-architect",
        description="PowerShell module and profile architecture specialist",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/powershell-module-architect",
        tags={"powershell", "modules", "psmodule", "gallery", "packaging"},
    ),

    "powershell-ui-architect": AgentDefinition(
        name="powershell-ui-architect",
        description="PowerShell UI/UX for WinForms, WPF, and TUIs",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/powershell-ui-architect",
        tags={"powershell", "ui", "winforms", "wpf", "tui", "console"},
    ),

    "refactoring-specialist": AgentDefinition(
        name="refactoring-specialist",
        description="Code refactoring expert for improving quality",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/refactoring-specialist",
        tags={"refactoring", "code-quality", "patterns", "clean-code", "restructuring"},
    ),

    "slack-expert": AgentDefinition(
        name="slack-expert",
        description="Slack platform and Bolt framework specialist",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/slack-expert",
        tags={"slack", "bolt", "chatbot", "webhooks", "workflows", "apps"},
    ),

    "tooling-engineer": AgentDefinition(
        name="tooling-engineer",
        description="Developer tooling specialist",
        category=AgentCategory.DEVELOPER_EXPERIENCE,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="developer-experience/tooling-engineer",
        tags={"tooling", "cli", "build-tools", "linters", "formatters", "generators"},
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 07. SPECIALIZED DOMAINS - 12 agents
    # ══════════════════════════════════════════════════════════════════════════

    "api-documenter": AgentDefinition(
        name="api-documenter",
        description="API documentation specialist",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.API, AgentCapability.DOCUMENTATION},
        profile_name="specialized-domains/api-documenter",
        tags={"api", "documentation", "openapi", "swagger", "rest", "graphql"},
    ),

    "blockchain-developer": AgentDefinition(
        name="blockchain-developer",
        description="Web3, blockchain, and smart contract developer",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.BACKEND},
        profile_name="specialized-domains/blockchain-developer",
        tags={"blockchain", "web3", "ethereum", "solidity", "smart-contracts", "defi"},
        model_routing="gpt-5.4",
    ),

    "embedded-systems": AgentDefinition(
        name="embedded-systems",
        description="Embedded and real-time systems expert",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.CPP},
        profile_name="specialized-domains/embedded-systems",
        tags={"embedded", "rtos", "arduino", "mcu", "bare-metal", "real-time"},
        model_routing="gpt-5.4",
    ),

    "fintech-engineer": AgentDefinition(
        name="fintech-engineer",
        description="Financial technology specialist",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.BACKEND},
        profile_name="specialized-domains/fintech-engineer",
        tags={"fintech", "payments", "banking", "trading", "compliance", "pii"},
        model_routing="gpt-5.4",
        reasoning_effort="high",
    ),

    "game-developer": AgentDefinition(
        name="game-developer",
        description="Game development expert for Unity, Unreal, Godot",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.CPP},
        profile_name="specialized-domains/game-developer",
        tags={"game", "unity", "unreal", "godot", "gamedev", "c#", "c++"},
    ),

    "iot-engineer": AgentDefinition(
        name="iot-engineer",
        description="IoT systems developer for connected devices",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.BACKEND},
        profile_name="specialized-domains/iot-engineer",
        tags={"iot", "mqtt", "sensors", "devices", "embedded", "raspberry-pi"},
    ),

    "m365-admin": AgentDefinition(
        name="m365-admin",
        description="Microsoft 365, Exchange, Teams, SharePoint admin",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.DEVOPS},
        profile_name="specialized-domains/m365-admin",
        tags={"m365", "sharepoint", "exchange", "teams", "azure-ad", "office365"},
    ),

    "mobile-app-developer": AgentDefinition(
        name="mobile-app-developer",
        description="Mobile application specialist for iOS and Android",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.MOBILE},
        profile_name="specialized-domains/mobile-app-developer",
        tags={"mobile", "ios", "android", "app", "react-native", "flutter"},
    ),

    "payment-integration": AgentDefinition(
        name="payment-integration",
        description="Payment systems integration specialist",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.BACKEND},
        profile_name="specialized-domains/payment-integration",
        tags={"payments", "stripe", "paypal", "PCI", "checkout", "billing"},
    ),

    "quant-analyst": AgentDefinition(
        name="quant-analyst",
        description="Quantitative analysis for finance",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.DATA},
        profile_name="specialized-domains/quant-analyst",
        tags={"quant", "quantitative", "trading", "algorithms", "python", "finance"},
        model_routing="gpt-5.4",
    ),

    "risk-manager": AgentDefinition(
        name="risk-manager",
        description="Risk assessment and management specialist",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="specialized-domains/risk-manager",
        tags={"risk", "assessment", "management", "mitigation", "compliance"},
    ),

    "seo-specialist": AgentDefinition(
        name="seo-specialist",
        description="Search engine optimization expert",
        category=AgentCategory.SPECIALIZED_DOMAINS,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="specialized-domains/seo-specialist",
        tags={"seo", "search", "optimization", "ranking", "keywords", "analytics"},
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 08. BUSINESS & PRODUCT - 11 agents
    # ══════════════════════════════════════════════════════════════════════════

    "business-analyst": AgentDefinition(
        name="business-analyst",
        description="Requirements specialist for business analysis",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/business-analyst",
        tags={"business-analysis", "requirements", "user-stories", "bpmn", "uml"},
    ),

    "content-marketer": AgentDefinition(
        name="content-marketer",
        description="Content marketing specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/content-marketer",
        tags={"content", "marketing", "copywriting", "seo", "social-media"},
    ),

    "customer-success-manager": AgentDefinition(
        name="customer-success-manager",
        description="Customer success and support specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/customer-success-manager",
        tags={"customer-success", "support", "onboarding", "retention", "csat"},
    ),

    "legal-advisor": AgentDefinition(
        name="legal-advisor",
        description="Legal and compliance advisor",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/legal-advisor",
        tags={"legal", "compliance", "contracts", "ip", "privacy", "gdpr"},
        model_routing="gpt-5.4",
        reasoning_effort="high",
    ),

    "product-manager": AgentDefinition(
        name="product-manager",
        description="Product strategy and roadmap specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/product-manager",
        tags={"product", "roadmap", "strategy", "prioritization", "agile", "scrum"},
    ),

    "project-manager": AgentDefinition(
        name="project-manager",
        description="Project management specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/project-manager",
        tags={"project-management", "pmp", "agile", "scrum", "kanban", "planning"},
    ),

    "sales-engineer": AgentDefinition(
        name="sales-engineer",
        description="Technical sales and pre-sales specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/sales-engineer",
        tags={"sales", "pre-sales", "technical", "demos", "solutions", "enterprise"},
    ),

    "scrum-master": AgentDefinition(
        name="scrum-master",
        description="Agile Scrum methodology specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/scrum-master",
        tags={"scrum", "agile", "sprint", "kanban", "ceremonies", "facilitation"},
    ),

    "technical-writer": AgentDefinition(
        name="technical-writer",
        description="Technical documentation specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.DOCUMENTATION},
        profile_name="business-product/technical-writer",
        tags={"technical-writing", "documentation", "guides", "manuals", "api-docs"},
    ),

    "ux-researcher": AgentDefinition(
        name="ux-researcher",
        description="User experience research specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FRONTEND},
        profile_name="business-product/ux-researcher",
        tags={"ux", "user-research", "usability", "testing", "personas", "journey-mapping"},
    ),

    "wordpress-master": AgentDefinition(
        name="wordpress-master",
        description="WordPress development and optimization specialist",
        category=AgentCategory.BUSINESS_PRODUCT,
        capabilities={AgentCapability.FULLSTACK},
        profile_name="business-product/wordpress-master",
        tags={"wordpress", "php", "plugins", "themes", "woocommerce", "cms"},
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 09. META & ORCHESTRATION - 12 agents
    # ══════════════════════════════════════════════════════════════════════════

    "agent-installer": AgentDefinition(
        name="agent-installer",
        description="Browse and install agents from repositories",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION},
        profile_name="meta-orchestration/agent-installer",
        tags={"agent", "installation", "registry", "plugins", "extensions"},
        model_routing="gpt-5.4",
    ),

    "agent-organizer": AgentDefinition(
        name="agent-organizer",
        description="Multi-agent coordination and organization",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION, AgentCapability.COORDINATION},
        profile_name="meta-orchestration/agent-organizer",
        tags={"multi-agent", "coordination", "orchestration", "workflow", "collaboration"},
        model_routing="gpt-5.4",
    ),

    "context-manager": AgentDefinition(
        name="context-manager",
        description="Context optimization and management expert",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION},
        profile_name="meta-orchestration/context-manager",
        tags={"context", "window", "tokens", "optimization", "compression", "summarization"},
    ),

    "error-coordinator": AgentDefinition(
        name="error-coordinator",
        description="Error handling and recovery specialist",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION},
        profile_name="meta-orchestration/error-coordinator",
        tags={"error-handling", "recovery", "resilience", "fallback", "retry"},
    ),

    "it-ops-orchestrator": AgentDefinition(
        name="it-ops-orchestrator",
        description="IT operations workflow orchestration specialist",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION, AgentCapability.DEVOPS},
        profile_name="meta-orchestration/it-ops-orchestrator",
        tags={"it-ops", "orchestration", "automation", "workflow", "runbook"},
    ),

    "knowledge-synthesizer": AgentDefinition(
        name="knowledge-synthesizer",
        description="Knowledge aggregation from multiple sources",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION, AgentCapability.RESEARCH},
        profile_name="meta-orchestration/knowledge-synthesizer",
        tags={"knowledge", "synthesis", "aggregation", "summarization", "insights"},
    ),

    "multi-agent-coordinator": AgentDefinition(
        name="multi-agent-coordinator",
        description="Advanced multi-agent orchestration specialist",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION, AgentCapability.COORDINATION},
        profile_name="meta-orchestration/multi-agent-coordinator",
        tags={"multi-agent", "orchestration", "swarm", "coordination", "delegation"},
        model_routing="gpt-5.4",
    ),

    "performance-monitor": AgentDefinition(
        name="performance-monitor",
        description="Agent performance optimization specialist",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION},
        profile_name="meta-orchestration/performance-monitor",
        tags={"performance", "monitoring", "metrics", "optimization", "latency"},
    ),

    "pied-piper": AgentDefinition(
        name="pied-piper",
        description="Orchestrate team of AI agents for repetitive workflows",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION, AgentCapability.COORDINATION},
        profile_name="meta-orchestration/pied-piper",
        tags={"workflow", "automation", "orchestration", "repetitive-tasks", "pipelines"},
        model_routing="gpt-5.4",
    ),

    "task-distributor": AgentDefinition(
        name="task-distributor",
        description="Task allocation and load balancing specialist",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION, AgentCapability.COORDINATION},
        profile_name="meta-orchestration/task-distributor",
        tags={"task-distribution", "load-balancing", "scheduling", "allocation"},
    ),

    "workflow-orchestrator": AgentDefinition(
        name="workflow-orchestrator",
        description="Complex workflow automation specialist",
        category=AgentCategory.META_ORCHESTRATION,
        capabilities={AgentCapability.ORCHESTRATION},
        profile_name="meta-orchestration/workflow-orchestrator",
        tags={"workflow", "automation", "orchestration", "dag", "pipelines", "airflow"},
    ),

    # ══════════════════════════════════════════════════════════════════════════
    # 10. RESEARCH & ANALYSIS - 7 agents
    # ══════════════════════════════════════════════════════════════════════════

    "competitive-analyst": AgentDefinition(
        name="competitive-analyst",
        description="Competitive intelligence specialist",
        category=AgentCategory.RESEARCH_ANALYSIS,
        capabilities={AgentCapability.RESEARCH},
        profile_name="research-analysis/competitive-analyst",
        tags={"competitive", "intelligence", "analysis", "market", "competitors"},
    ),

    "data-researcher": AgentDefinition(
        name="data-researcher",
        description="Data discovery and analysis expert",
        category=AgentCategory.RESEARCH_ANALYSIS,
        capabilities={AgentCapability.RESEARCH, AgentCapability.DATA},
        profile_name="research-analysis/data-researcher",
        tags={"research", "data", "discovery", "analysis", "datasets"},
    ),

    "docs-researcher": AgentDefinition(
        name="docs-researcher",
        description="Documentation-backed API and framework verification",
        category=AgentCategory.RESEARCH_ANALYSIS,
        capabilities={AgentCapability.RESEARCH},
        profile_name="research-analysis/docs-researcher",
        tags={"documentation", "research", "api", "verification", "reference"},
    ),

    "market-researcher": AgentDefinition(
        name="market-researcher",
        description="Market analysis and consumer insights",
        category=AgentCategory.RESEARCH_ANALYSIS,
        capabilities={AgentCapability.RESEARCH},
        profile_name="research-analysis/market-researcher",
        tags={"market", "research", "analysis", "consumer", "insights", "trends"},
    ),

    "research-analyst": AgentDefinition(
        name="research-analyst",
        description="Comprehensive research specialist",
        category=AgentCategory.RESEARCH_ANALYSIS,
        capabilities={AgentCapability.RESEARCH},
        profile_name="research-analysis/research-analyst",
        tags={"research", "analysis", "methodology", "synthesis", "findings"},
        model_routing="gpt-5.4",
    ),

    "search-specialist": AgentDefinition(
        name="search-specialist",
        description="Advanced information retrieval expert",
        category=AgentCategory.RESEARCH_ANALYSIS,
        capabilities={AgentCapability.RESEARCH},
        profile_name="research-analysis/search-specialist",
        tags={"search", "information-retrieval", "vector-search", "semantic", "indexing"},
    ),

    "trend-analyst": AgentDefinition(
        name="trend-analyst",
        description="Emerging trends and forecasting specialist",
        category=AgentCategory.RESEARCH_ANALYSIS,
        capabilities={AgentCapability.RESEARCH},
        profile_name="research-analysis/trend-analyst",
        tags={"trends", "forecasting", "analysis", "future", "predictions", "emerging"},
    ),
}


# ─────────────────────────────────────────────────────────────────────────────
# Resolve the canonical AGENT_REGISTRY at import time.
# YAML wins when present and parseable; otherwise the embedded literal is used.
# This keeps existing callers (``from myclaw.agents.registry import AGENT_REGISTRY``)
# working unchanged regardless of which source provided the data.
# ─────────────────────────────────────────────────────────────────────────────

_yaml_loaded = load_agents_from_yaml()
if _yaml_loaded:
    AGENT_REGISTRY: Dict[str, AgentDefinition] = _yaml_loaded
    logger.info("Loaded %d agents from %s", len(AGENT_REGISTRY), AGENT_DATA_FILE)
else:
    AGENT_REGISTRY = _LITERAL_AGENT_REGISTRY
    logger.info(
        "Loaded %d agents from embedded literal (YAML at %s missing/invalid)",
        len(AGENT_REGISTRY), AGENT_DATA_FILE,
    )


def get_agent(name: str) -> Optional[AgentDefinition]:
    """Get an agent definition by name."""
    return AGENT_REGISTRY.get(name)


def list_agents(
    category: Optional[AgentCategory] = None,
    capability: Optional[AgentCapability] = None,
    tags: Optional[List[str]] = None,
    query: Optional[str] = None
) -> List[AgentDefinition]:
    """List agents filtered by various criteria."""
    results = list(AGENT_REGISTRY.values())

    if category:
        results = [a for a in results if a.category == category]

    if capability:
        results = [a for a in results if capability in a.capabilities]

    if tags:
        results = [a for a in results if any(t in a.tags for t in tags)]

    if query:
        results = [a for a in results if a.matches_query(query)]

    return results


def list_agents_by_category() -> Dict[AgentCategory, List[AgentDefinition]]:
    """List all agents grouped by category."""
    grouped: Dict[AgentCategory, List[AgentDefinition]] = {
        cat: [] for cat in AgentCategory
    }
    for agent in AGENT_REGISTRY.values():
        grouped[agent.category].append(agent)
    return grouped


def get_agent_count() -> int:
    """Get total number of registered agents."""
    return len(AGENT_REGISTRY)


def get_categories_with_count() -> List[tuple]:
    """Get list of categories with agent counts."""
    grouped = list_agents_by_category()
    return [(cat.value, len(agents)) for cat, agents in grouped.items()]
