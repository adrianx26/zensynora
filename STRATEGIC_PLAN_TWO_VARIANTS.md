# ZenSynora Strategic Plan: Two-Variant Architecture

> **Prepared for:** ZenSynora (MyClaw) codebase at `C:\ANTI\zensynora`  
> **Date:** 2026-04-20  
> **Scope:** Code analysis, two architectural variants, migration roadmap, and implementation priorities.

---

## 1. Current Codebase Analysis

### 1.1 What the App Does

**ZenSynora (MyClaw)** is a multi-agent AI orchestration platform designed as a "personal AI agent." It provides:

- **Unified LLM Gateway** — Abstraction over Ollama, OpenAI, Anthropic, Gemini, Groq, OpenRouter, LM Studio, and llama.cpp.
- **Multi-Agent Swarms** — Parallel, sequential, and hierarchical coordination strategies with aggregation engines (consensus, best-pick, synthesis).
- **Persistent Knowledge System** — Markdown-based notes with SQLite FTS5 full-text search, BM25 ranking, entity/relation extraction, and knowledge graphs.
- **Dynamic Tooling** — 40+ built-in tools (shell sandboxed execution, web browse/search, file I/O, GitHub integration, scheduling, SSH, etc.) plus a `TOOLBOX` for custom user skills.
- **Security-First Design** — Sandboxed shell execution (`ALLOWED_COMMANDS` / `BLOCKED_COMMANDS`), path validation, tamper-evident audit logs, rate limiting (token bucket).
- **Hardware-Aware Optimization** — Deep telemetry-driven model selection for local inference (CPU/GPU/RAM detection).
- **Web UI + Channels** — React + Vite + TypeScript frontend with WebSocket streaming; Telegram and WhatsApp bot channels.
- **Specialized Agents** — Medic Agent (health/integrity monitoring), NewTech Agent (AI news tracking), Skill Adapter (agentskills.io compatibility).
- **Cross-Platform Backends** — Local, Docker, SSH, and WSL2 execution environments.

### 1.2 Current Tech Stack

| Layer | Technology |
|-------|------------|
| **Runtime** | Python 3.12, asyncio |
| **Web Backend** | FastAPI |
| **Frontend** | React 18, Vite, TypeScript, WebSocket |
| **Database** | SQLite (per-user files: `memory_{user_id}.db`, `knowledge_{user_id}.db`, `metering.db`, `mfa.db`, `knowledge_spaces.db`) |
| **Search** | SQLite FTS5 with BM25 ranking |
| **Caching** | In-memory LRU + TTL, optional semantic cache (sentence-transformers), optional Redis |
| **Config** | JSON file (`~/.myclaw/config.json`) with Pydantic validation |
| **Auth** | TOTP/MFA (opt-in, SQLite-backed) — **no full user management** |
| **Deployment** | Docker multi-stage build, Docker Compose (optional Redis + Ollama) |
| **Testing** | pytest, pytest-asyncio (partial coverage) |

### 1.3 Architecture at a Glance

```
User (CLI / Telegram / WhatsApp / Web UI)
  │
  ▼
Gateway Router  ──►  Agent Core  ──►  LLM Provider(s)
  │                    │                  │
  │                    ├─► Memory (SQLite per-user)
  │                    ├─► Knowledge Base (SQLite FTS5)
  │                    ├─► Tools (Sandboxed execution)
  │                    ├─► Swarm Orchestrator
  │                    ├─► Task Scheduler
  │                    └─► Caching (LRU / Semantic / Redis)
  │
  └─► Admin Dashboard, Metrics, Cost Tracker
```

### 1.4 Key Pain Points for Multi-User / Enterprise Scale

| # | Pain Point | Current State | Risk at Scale |
|---|-----------|---------------|---------------|
| 1 | **Storage Model** | Per-user SQLite files in `~/.myclaw` on local filesystem | Filesystem I/O bottleneck; no horizontal scaling; backup/restore is manual per-file |
| 2 | **Authentication** | Only TOTP/MFA exists; no user registration, login, sessions, or OAuth/SSO | Cannot onboard teams; no identity provider integration |
| 3 | **Authorization** | Basic 3-role RBAC in Knowledge Spaces only (`viewer`/`editor`/`admin`) | No granular permissions on tools, agents, providers, or system config |
| 4 | **Tenant Isolation** | `user_id` is passed around but not enforced at the database layer | Data leakage risk; no schema-level isolation |
| 5 | **Agent Registry** | Global in-memory dict (`_agent_registry`) | Lost on restart; not shareable across workers; no per-tenant scoping |
| 6 | **Configuration** | Single JSON file (`config.json`) | Cannot vary config per tenant; hot-reload is file-watcher based |
| 7 | **Frontend Session** | `localStorage` for chat history; WebSocket has no auth handshake | No multi-user session management; chat history is device-bound |
| 8 | **Rate Limiting** | Per-tool in-memory token bucket (with Redis advisory sync) | Not per-user; easily bypassed without distributed enforcement |
| 9 | **API Design** | FastAPI with flat URL structure; no API versioning | Breaking changes affect all clients; no client key management |
| 10 | **Observability** | Basic Prometheus metrics, console logging | No structured JSON logs, no distributed tracing, no log aggregation |
| 11 | **Concurrency** | SQLite WAL mode helps, but still single-writer per DB | Concurrent writes from many users will queue and degrade |
| 12 | **Secrets Management** | API keys stored in `config.json` (with optional encryption) | No rotation, no per-tenant secrets, no external vault integration |

---

## 2. Two Architectural Variants

### Variant A: ZenSynora Personal (Solo Power User)

> **Tagline:** *Your AI, on your machine, under your control.*

**Target Audience:** Individual developers, researchers, privacy-conscious power users who want a local-first AI assistant.

**Guiding Principles:**
- **Local-first:** All data stays on the user's machine.
- **Zero external dependencies:** Works without Redis, cloud APIs, or internet (if using Ollama).
- **Simplicity:** Single binary / container; minimal configuration.
- **Privacy:** No telemetry, no user tracking, no cloud sync.

**Architecture:**

```
┌─────────────────────────────────────────────────────────┐
│              ZenSynora Personal (Single Node)            │
│                                                          │
│   ┌──────────┐   ┌──────────┐   ┌──────────────────┐   │
│   │  CLI     │   │ Web UI   │   │ Telegram/WhatsApp│   │
│   └────┬─────┘   └────┬─────┘   └────────┬─────────┘   │
│        └──────────────┼──────────────────┘              │
│                       ▼                                  │
│              ┌─────────────────┐                         │
│              │  FastAPI (1 proc)│                         │
│              └────────┬────────┘                         │
│                       ▼                                  │
│        ┌──────────────────────────────┐                  │
│        │     Agent Core (asyncio)     │                  │
│        │  • In-memory agent registry  │                  │
│        │  • LRU + semantic cache      │                  │
│        │  • SQLite pools (WAL mode)   │                  │
│        └──────────┬───────────────────┘                  │
│                   │                                      │
│        ┌──────────┴──────────┐                          │
│        ▼                     ▼                          │
│   ┌──────────┐        ┌──────────┐                      │
│   │ SQLite   │        │ Local    │                      │
│   │ (per-user│        │ LLM      │                      │
│   │  files)  │        │ (Ollama) │                      │
│   └──────────┘        └──────────┘                      │
│                                                          │
│   Storage: ~/.myclaw/                                    │
│   Config:  ~/.myclaw/config.json                         │
└─────────────────────────────────────────────────────────┘
```

**Tech Stack (Minimal):**
- Python 3.12 + FastAPI (single process, no workers)
- SQLite (WAL mode enabled)
- React frontend served as static files
- Optional: Ollama for local LLMs
- No Redis, no PostgreSQL, no external auth provider

**Key Retention from Current Codebase:**
- Keep per-user SQLite files (perfect for solo use).
- Keep JSON config file (simple to edit).
- Keep in-memory caches (fast, no network overhead).
- Keep localStorage chat history (device-bound is a feature for privacy).

**Optimizations to Add:**
- **Desktop wrapper:** Tauri or Electron shell for a native app feel.
- **Offline mode:** Graceful degradation when internet is unavailable.
- **Auto-backup:** Scheduled zip export of `~/.myclaw` to a user-chosen folder.
- **Local encryption at rest:** Encrypt SQLite files with a user passphrase.

---

### Variant B: ZenSynora Team / Enterprise (Multi-Tenant SaaS)

> **Tagline:** *AI agents for every team, with governance, scale, and compliance.*

**Target Audience:** Engineering teams, enterprises, MSPs, and SaaS builders who need multi-user AI orchestration with security and compliance guardrails.

**Guiding Principles:**
- **Multi-tenancy:** True tenant isolation at the database and API layer.
- **Scalability:** Horizontally scalable stateless API layer.
- **Governance:** Granular RBAC, audit trails, compliance exports (SOC2, GDPR).
- **Integrations:** SSO (SAML/OIDC), SCIM provisioning, Slack/Teams bots.

**Architecture:**

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ZenSynora Enterprise                             │
│                                                                          │
│   Ingress                                                                │
│   ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│   │  Web App    │  │  Mobile App │  │ Slack/Teams │  │  API Clients│   │
│   └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│          └─────────────────┴─────────────────┴─────────────────┘         │
│                                    │                                     │
│                              ┌─────┴─────┐                               │
│                              │  CDN / WAF │                               │
│                              └─────┬─────┘                               │
│                                    ▼                                     │
│   ┌──────────────────────────────────────────────────────────────┐      │
│   │                     Load Balancer (NGINX / ALB)               │      │
│   └──────────────────────────────────────────────────────────────┘      │
│                                    │                                     │
│          ┌─────────────────────────┼─────────────────────────┐          │
│          ▼                         ▼                         ▼          │
│   ┌──────────────┐         ┌──────────────┐         ┌──────────────┐   │
│   │ FastAPI      │         │ FastAPI      │         │ FastAPI      │   │
│   │ Worker 1     │         │ Worker 2     │         │ Worker N     │   │
│   │ (stateless)  │         │ (stateless)  │         │ (stateless)  │   │
│   └──────┬───────┘         └──────┬───────┘         └──────┬───────┘   │
│          │                        │                        │           │
│          └────────────────────────┼────────────────────────┘           │
│                                   ▼                                    │
│   ┌──────────────────────────────────────────────────────────────┐    │
│   │  Shared State & Cache Layer                                   │    │
│   │  • Redis (agent registry, rate limits, sessions, pub/sub)    │    │
│   │  • Celery / RQ (background jobs: newtech scans, medic checks)│    │
│   └──────────────────────────────────────────────────────────────┘    │
│                                   │                                    │
│          ┌────────────────────────┼────────────────────────┐          │
│          ▼                        ▼                        ▼          │
│   ┌──────────────┐       ┌──────────────┐       ┌──────────────┐    │
│   │ PostgreSQL   │       │ PostgreSQL   │       │   S3 / MinIO │    │
│   │ (Users,      │       │ (Tenant      │       │   (File      │    │
│   │  Auth,       │       │  Data — per  │       │   storage,   │    │
│   │  Config)     │       │  schema)     │       │   exports)   │    │
│   └──────────────┘       └──────────────┘       └──────────────┘    │
│                                                                          │
│   Supporting Services                                                    │
│   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │
│   │  Keycloak    │  │  OpenSearch  │  │  Prometheus  │                │
│   │  (SSO/SAML)  │  │  (FTS + Vec) │  │  + Grafana   │                │
│   └──────────────┘  └──────────────┘  └──────────────┘                │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

**Tech Stack (Enterprise):**
- **API Layer:** FastAPI with Uvicorn workers (stateless, horizontally scalable)
- **Primary Database:** PostgreSQL 15+ (per-tenant schema isolation or row-level security)
- **Search:** OpenSearch or pgvector + PostgreSQL full-text search (replace SQLite FTS5)
- **Cache & State:** Redis Cluster (agent registry, sessions, rate limits, pub/sub)
- **Queue:** Celery with Redis or RabbitMQ (background tasks)
- **Auth:** Keycloak / Auth0 / Okta (OIDC + SAML 2.0)
- **Object Storage:** S3-compatible (MinIO, AWS S3, Cloudflare R2) for files, exports, audit archives
- **Observability:** Prometheus metrics, Grafana dashboards, structured JSON logging (OpenTelemetry tracing)
- **Secrets:** HashiCorp Vault or AWS Secrets Manager integration

**Key Differences from Personal Variant:**

| Concern | Personal | Enterprise |
|---------|----------|------------|
| **Users** | 1 local user | Unlimited tenants & users |
| **Auth** | TOTP only | SSO (OIDC/SAML), SCIM, API keys |
| **Database** | SQLite files | PostgreSQL (per-tenant schemas) |
| **Search** | SQLite FTS5 | OpenSearch or pgvector |
| **Cache** | In-memory LRU | Redis Cluster |
| **State** | In-memory dict | Redis-backed StateStore |
| **File Storage** | Local filesystem | S3-compatible object store |
| **Config** | JSON file | Database-driven per-tenant config |
| **RBAC** | 3 roles (spaces only) | Granular resource-level permissions |
| **Audit** | Tamper-evident local log | Centralized immutable audit stream |
| **API** | Flat, unversioned | Versioned (`/v1/...`), rate-limited, keyed |
| **Deployment** | Docker Compose | Kubernetes (Helm charts) |
| **LLM Routing** | User picks provider | Admin-configured per-team quotas & allowed providers |
| **Cost Control** | Basic monthly tracking | Per-tenant, per-user, per-project cost allocation |
| **Compliance** | None | SOC2/GDPR export, data retention policies |

---

## 3. Implementation Roadmap

### Phase 1: Foundation (Weeks 1–4) — **Critical Path**

**Goal:** Establish the enterprise data layer and identity model without breaking the personal variant.

| Task | Module | Effort | Notes |
|------|--------|--------|-------|
| **1.1 Abstract Database Layer** | `myclaw/db/` | 3d | Create `DatabaseBackend` ABC with `SQLiteBackend` (default) and `PostgresBackend`. All existing SQLite code moves behind this interface. |
| **1.2 Tenant-Aware Schema Design** | `myclaw/db/schema/` | 4d | Design PostgreSQL schemas: `public` (users, auth, tenants), `tenant_{id}` (isolated data). For SQLite, keep current flat files but wrap with tenant context. |
| **1.3 User & Identity Model** | `myclaw/identity/` | 5d | `User`, `Tenant`, `Membership` tables. Support local auth (Personal) and OIDC (Enterprise). |
| **1.4 Configuration Refactor** | `myclaw/config.py` | 3d | Split into `UserConfig` ( Personal, JSON file) and `TenantConfig` (Enterprise, DB-backed). |
| **1.5 API Versioning & Auth Middleware** | `myclaw/web/api.py` | 3d | Add `/v1/` prefix. Implement JWT/OAuth2 dependency injection for Enterprise; no-op for Personal. |

**Deliverable:** Both variants compile and pass tests. Personal behavior is unchanged.

---

### Phase 2: Enterprise Core (Weeks 5–8)

**Goal:** Build the multi-tenant engine.

| Task | Module | Effort | Notes |
|------|--------|--------|-------|
| **2.1 PostgreSQL Backend Implementation** | `myclaw/db/postgres.py` | 5d | Implement all CRUD, FTS (using `tsvector` / `pg_trgm`), and WAL-equivalent patterns. |
| **2.2 Redis StateStore Completion** | `myclaw/state_store.py` | 4d | Finish `RedisStateStore` for agent registry, rate limiting, sessions, and pub/sub for knowledge sync. |
| **2.3 Granular RBAC** | `myclaw/rbac.py` | 5d | Resource-level permissions: `agent:create`, `tool:shell:execute`, `provider:openai:use`, `knowledge_space:admin`. |
| **2.4 API Key Management** | `myclaw/web/api_keys.py` | 3d | Scoped keys with expiration, rate limits, and audit trails. |
| **2.5 Tenant Isolation Middleware** | `myclaw/web/middleware.py` | 2d | Auto-set `search_path` to `tenant_{id}` for PostgreSQL; validate JWT tenant claim. |

**Deliverable:** Enterprise variant can onboard a tenant, create users, assign roles, and isolate data.

---

### Phase 3: Scaling & Reliability (Weeks 9–12)

**Goal:** Make the enterprise variant production-ready.

| Task | Module | Effort | Notes |
|------|--------|--------|-------|
| **3.1 Celery Background Workers** | `myclaw/workers/` | 4d | Move NewTech scans, Medic health checks, and KB indexing to async workers. |
| **3.2 OpenSearch Integration** | `myclaw/search/` | 5d | Replace SQLite FTS5 for Enterprise. Hybrid search: BM25 + vector embeddings. |
| **3.3 S3 Object Storage** | `myclaw/storage.py` | 3d | Abstract file I/O. Personal uses local filesystem; Enterprise uses S3/MinIO. |
| **3.4 Secrets Vault Integration** | `myclaw/secrets.py` | 3d | Integrate HashiCorp Vault or AWS Secrets Manager for API keys and credentials. |
| **3.5 Advanced Metering & Quotas** | `myclaw/metering.py` | 3d | Per-tenant, per-project, per-user quotas with real-time dashboard updates. |
| **3.6 Compliance & Audit Exports** | `myclaw/compliance.py` | 3d | Immutable audit log streaming to S3; GDPR data export; SOC2 evidence collection. |

**Deliverable:** Horizontal scaling works; background jobs run; search is fast at 10M+ documents.

---

### Phase 4: Frontend & Experience (Weeks 13–16)

**Goal:** Build a multi-user web experience.

| Task | Module | Effort | Notes |
|------|--------|--------|-------|
| **4.1 Auth UI (Login / SSO)** | `webui/src/auth/` | 4d | OAuth2 redirect flow, MFA enrollment, session refresh. |
| **4.2 Tenant Switcher & Admin Panel** | `webui/src/admin/` | 5d | Tenant creation, member management, role assignment, provider quota settings. |
| **4.3 Server-Side Chat History** | `webui/src/chat/` | 3d | Replace `localStorage` with server-persisted threads; real-time sync across devices. |
| **4.4 Audit Log Explorer** | `webui/src/audit/` | 3d | Searchable, filterable, tamper-evident log viewer with export. |
| **4.5 Visual Swarm Composer** | `webui/src/swarm/` | 5d | Drag-and-drop agent swarm builder with live telemetry. |

**Deliverable:** Enterprise web UI is feature-complete for team administrators and end users.

---

### Phase 5: Hardening & Launch (Weeks 17–20)

| Task | Module | Effort | Notes |
|------|--------|--------|-------|
| **5.1 Kubernetes Helm Charts** | `deploy/helm/` | 4d | Charts for API workers, Redis, PostgreSQL, OpenSearch, Celery workers. |
| **5.2 Load Testing & Optimization** | `benchmarks/` | 3d | k6 or Locust tests for 1,000 concurrent users; optimize DB connection pooling. |
| **5.3 Disaster Recovery** | `myclaw/backup.py` | 3d | Automated PostgreSQL backups (WAL archiving), point-in-time recovery. |
| **5.4 Documentation & SDK** | `docs/enterprise/` | 3d | API reference (OpenAPI), Python SDK for external integrations, deployment runbooks. |
| **5.5 Security Audit** | — | 5d | Penetration test, dependency scanning (Snyk), SBOM generation. |

**Deliverable:** Enterprise variant is production-ready for GA launch.

---

## 4. Migration Path: Personal → Enterprise

### Strategy: **Shared Core, Swappable Backends**

The most important architectural decision is to keep a **single codebase** with **backend abstraction**. This avoids a painful fork.

```python
# myclaw/db/factory.py
from .sqlite_backend import SQLiteBackend
from .postgres_backend import PostgresBackend

def get_db_backend():
    if config.database.engine == "postgresql":
        return PostgresBackend(config.database.url)
    return SQLiteBackend(config.database.path)
```

### Migration Steps for an Existing Personal User

1. **Export** — Personal user runs `zensynora export --format=jsonl` to dump all SQLite databases.
2. **Provision Tenant** — Enterprise admin creates a tenant and invites the user.
3. **Import** — Enterprise API accepts the JSONL dump and imports into PostgreSQL (with tenant schema isolation).
4. **Redirect** — User updates their local config or mobile app to point to the enterprise API endpoint.
5. **Parallel Run** (optional) — Personal instance can run in read-only mode as a local backup during transition.

### Code Refactoring Priority

| Refactor Order | File / Module | Action |
|----------------|---------------|--------|
| 1 | `myclaw/memory.py` | Inject `DatabaseBackend` instead of direct `aiosqlite` calls. |
| 2 | `myclaw/knowledge/db.py` | Same abstraction; FTS5 becomes pluggable (`SQLiteFTSBackend`, `OpenSearchBackend`). |
| 3 | `myclaw/config.py` | Split into `PersonalConfig` (JSON) and `TenantConfig` (DB). Use factory pattern. |
| 4 | `myclaw/web/api.py` | Add versioning, dependency-injected auth, and tenant middleware. |
| 5 | `myclaw/agent.py` | Pass `tenant_id` and `user_id` explicitly; remove global `_agent_registry`. |
| 6 | `myclaw/tools/core.py` | Inject `UserContext` into every tool call for RBAC enforcement. |
| 7 | `myclaw/state_store.py` | Complete `RedisStateStore`; make it the default in Enterprise Docker Compose. |

---

## 5. Specific Recommendations

### 5.1 Database Schema (Enterprise)

**Tenant Isolation Pattern: Schema-per-Tenant**

```sql
-- public schema: shared metadata
CREATE TABLE public.tenants (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE public.users (
    id UUID PRIMARY KEY,
    tenant_id UUID REFERENCES public.tenants(id),
    email TEXT UNIQUE NOT NULL,
    auth_provider TEXT NOT NULL DEFAULT 'local',
    external_id TEXT,  -- OIDC sub claim
    role TEXT NOT NULL DEFAULT 'member',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- tenant_{id} schema: isolated data
CREATE SCHEMA IF NOT EXISTS tenant_abc123;

CREATE TABLE tenant_abc123.conversations (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL,
    title TEXT,
    model TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE tenant_abc123.messages (
    id UUID PRIMARY KEY,
    conversation_id UUID REFERENCES tenant_abc123.conversations(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_calls JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Full-text search using pg_trgm + tsvector
CREATE INDEX idx_messages_fts ON tenant_abc123.messages
USING GIN (to_tsvector('english', content));
```

**Why schema-per-tenant?**
- Stronger isolation than row-level security (RLS) alone.
- Easier per-tenant backup/restore.
- Query plans are simpler (no `tenant_id` filter on every index).
- Slight overhead at high tenant counts (>10,000) — acceptable for B2B.

---

### 5.2 API Design (Enterprise)

**Versioned, Resource-Oriented Endpoints:**

```
POST   /v1/auth/token          # OAuth2 password flow or SSO callback
POST   /v1/auth/refresh
DELETE /v1/auth/token

GET    /v1/agents              # List agents (scoped to tenant)
POST   /v1/agents
GET    /v1/agents/{id}
POST   /v1/agents/{id}/chat    # Non-streaming chat
WS     /v1/agents/{id}/stream  # WebSocket streaming

GET    /v1/knowledge           # Search knowledge base
POST   /v1/knowledge/notes
GET    /v1/knowledge/notes/{id}

GET    /v1/spaces              # Knowledge spaces
POST   /v1/spaces/{id}/members

GET    /v1/admin/audit          # Audit logs (admin only)
GET    /v1/admin/metering       # Usage dashboard
POST   /v1/admin/quotas

GET    /v1/tools               # List available tools (filtered by RBAC)
POST   /v1/tools/{name}/invoke  # Invoke tool with rate-limit headers
```

**Standard Headers:**

```
X-Request-ID: uuid          # Distributed tracing
X-Tenant-ID: abc123         # Set by middleware (from JWT)
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 87
X-RateLimit-Reset: 1713623400
```

---

### 5.3 Infrastructure (Enterprise)

**Minimum Viable Production Stack:**

| Component | Choice | Reason |
|-----------|--------|--------|
| **Orchestration** | Kubernetes (EKS / GKE / AKS) | Standard for B2B SaaS; Helm charts for reproducibility. |
| **Database** | PostgreSQL 15+ (managed: RDS / Cloud SQL) | ACID compliance, mature tooling, JSONB for flexibility. |
| **Cache** | Redis 7 (Elasticache / MemoryDB) | Pub/sub for real-time KB sync; cluster mode for HA. |
| **Search** | OpenSearch 2.x (managed) | BM25 + k-NN vector search in one engine; drop-in for SQLite FTS5 concepts. |
| **Queue** | Celery + Redis | Python-native; good for periodic tasks (NewTech, Medic). |
| **Object Storage** | S3 or Cloudflare R2 | Cheap, durable, infinite scale for exports and audit archives. |
| **Auth** | Keycloak or Auth0 | OIDC + SAML out of the box; SCIM provisioning. |
| **Observability** | Prometheus + Grafana + Loki + Tempo | Metrics, logs, and traces in one stack. |
| **Secrets** | HashiCorp Vault or AWS Secrets Manager | Rotation, leasing, audit. |

**Resource Estimates (per 1,000 active users):**

| Service | CPU | Memory | Storage |
|---------|-----|--------|---------|
| FastAPI (3 replicas) | 3 cores | 6 GB | — |
| Celery Workers (2 pods) | 2 cores | 4 GB | — |
| PostgreSQL | 2 cores | 8 GB | 200 GB SSD |
| Redis | 1 core | 2 GB | — |
| OpenSearch | 2 cores | 8 GB | 100 GB SSD |

---

### 5.4 RBAC Matrix (Enterprise)

| Resource | Action | Admin | Editor | Member | Viewer |
|----------|--------|-------|--------|--------|--------|
| Agent | create | ✅ | ❌ | ❌ | ❌ |
| Agent | chat | ✅ | ✅ | ✅ | ✅ |
| Agent | delete | ✅ | own | own | ❌ |
| Tool:shell | execute | ✅ | ✅ | ❌ | ❌ |
| Tool:web | execute | ✅ | ✅ | ✅ | ❌ |
| Knowledge Space | admin | ✅ | ❌ | ❌ | ❌ |
| Knowledge Space | write | ✅ | ✅ | ❌ | ❌ |
| Knowledge Space | read | ✅ | ✅ | ✅ | ✅ |
| Provider | configure | ✅ | ❌ | ❌ | ❌ |
| Audit Log | read | ✅ | ❌ | ❌ | ❌ |
| User | invite | ✅ | ❌ | ❌ | ❌ |
| Quota | set | ✅ | ❌ | ❌ | ❌ |

**Implementation:** Store permissions as tuples `(resource_type, resource_id, action, user_id)` in PostgreSQL; cache hot ACLs in Redis.

---

### 5.5 Frontend State Management (Enterprise)

Replace `localStorage` chat history with a server-backed model:

```typescript
// Server-persisted conversation thread
interface Conversation {
  id: string;
  tenant_id: string;
  user_id: string;
  agent_id: string;
  title: string;
  messages: ChatMessage[];
  created_at: string;
  updated_at: string;
}

// Sync strategy: Optimistic UI + WebSocket real-time
// - User sends message → render immediately
// - WebSocket streams assistant response → append chunks
// - On reconnect, fetch missing messages by `updated_at` cursor
```

---

## 6. Summary & Next Steps

### Immediate Actions (This Week)

1. **Merge the database abstraction PR** — Create `myclaw/db/backends/` and move all raw SQLite calls behind an ABC.
2. **Tag a `v0.5.0-personal` release** — Freeze the Personal variant so it remains stable.
3. **Create the `enterprise` feature branch** — All multi-tenant work happens here until GA.

### Decision Gates

| Gate | Question | Go / No-Go Criteria |
|------|----------|---------------------|
| **G1** (Week 4) | Is the DB abstraction solid? | 100% test pass on both SQLite and Postgres backends. |
| **G2** (Week 8) | Can we onboard a tenant? | End-to-end: signup → create agent → chat → audit log entry. |
| **G3** (Week 12) | Does it scale to 100 concurrent users? | Load test passes p99 < 2s for chat completion. |
| **G4** (Week 16) | Is the frontend complete? | UX audit passes; no `localStorage` for business data. |
| **G5** (Week 20) | Is it secure enough for GA? | Pen test complete; no critical or high vulnerabilities. |

### Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| SQLite → PostgreSQL migration is buggier than expected | Medium | High | Keep SQLite as default for 2 more releases; run dual-write during beta. |
| Frontend rewrite takes longer than planned | Medium | Medium | Ship Enterprise with read-only admin panel first; iterative UX improvements. |
| OpenSearch cost is too high for small teams | Low | Medium | Offer PostgreSQL `tsvector` as a "lite search" fallback in Enterprise. |
| Multi-tenant schema performance degrades | Low | High | Load test early (G3); switch to Citus or row-level security if schema count > 5,000. |

---

*End of Strategic Plan*
