# Optimization Plan (planx1.md)

## 1️⃣ Python Codebase

| File / Area | Issue | Suggested Fix |
|-------------|-------|---------------|
| `cli.py` & other entry‑points | Heavy imports on every command (e.g., `pandas`). | Lazy‑load optional dependencies inside command handlers. |
| `tests/` | Duplicate fixture setup across many tests. | Create shared pytest fixtures in `conftest.py` (common `client`, `temp_dir`). |
| `dead-code-report.md` | Unused imports & functions. | Run `autoflake --remove-all-unused-imports -i` and delete dead functions after verification. |
| Logging | `logging.basicConfig` called in multiple modules → duplicate handlers. | Centralize logging configuration in a single `logging_config.py` and import it everywhere. |
| Exception handling | Generic `Exception` re‑raised. | Define a custom hierarchy (`ZensynoraError`, `ConfigError`, …) and raise specific types. |
| `deploy.py` | `subprocess.run` with `shell=True` for simple commands. | Use `subprocess.run([...], check=True)` to avoid shell injection risk. |
| `eval/` | Re‑computes large embeddings on every test run. | Cache computed embeddings in a temp directory and reuse when unchanged. |
| `docs/` | Duplicated markdown sections across several guides. | Consolidate common parts into `_includes/` and use a pre‑processor (`mkdocs` + `markdown-include`). |
| `graphify-out/` | Large cached JSON files are version‑controlled. | Add cache directory to `.gitignore` and generate on‑demand (`graphify update .`). |

## 2️⃣ Frontend (React / TypeScript)

| File / Area | Issue | Suggested Fix |
|-------------|-------|---------------|
| `webui/src/main.tsx` | Imports whole `lodash` for a single utility. | Switch to modular import (`import debounce from 'lodash/debounce'`). |
| CSS | Global selectors in `index.css` cause leakage. | Scope styles via CSS modules or Tailwind and prune unused selectors (`grep -R 'body '`). |
| Vite config | `optimizeDeps` includes many large libs not used at runtime. | Remove unnecessary entries to shrink bundle size. |
| Component re‑renders | Some stable‑prop components lack memoization (e.g., `AgentStatus`). | Add `React.memo` or `useMemo` where profiling shows a bottleneck. |
| Type safety | Several `any` typings in API responses. | Generate TypeScript interfaces from OpenAPI spec (`npm run gen:api`). |
| Testing | UI tests run full end‑to‑end for simple unit logic. | Split into unit (`@testing-library/react`) and integration tests to speed CI. |

## 3️⃣ CI / DevOps

| Area | Issue | Suggested Fix |
|------|-------|---------------|
| GitHub Actions (`ci.yml`) | Runs `pytest` with `--maxfail=5` but continues on failures. | Use `fail-fast: true` and limit matrix jobs to needed configurations. |
| Dockerfile | Installs dev dependencies (`pytest`, `pre‑commit`) in production image. | Separate build stage: `builder` installs dev tools, final stage copies only runtime packages. |
| pre‑commit | Lints whole repo each commit (e.g., `black`). | Configure `black` to run only on staged files (`--skip-string-normalization`). |
| `install.sh` | Uses `sudo` unconditionally; fails on Windows CI runners. | Detect OS and apply sudo conditionally, or use cross‑platform `pip install -e .`. |
| Cache | No caching of pip wheels or npm packages, causing long CI times. | Add `actions/cache` steps for `~/.cache/pip` and `node_modules`. |

## 4️⃣ Documentation & Knowledge Graph

| Area | Issue | Suggested Fix |
|------|-------|---------------|
| `graphify-out/` | Cached AST JSON files tracked in git, inflating repo size. | Add `graphify-out/cache/` to `.gitignore`; regenerate when needed (`graphify update .`). |
| Docs | Overlapping sections across several markdown files (`MEDIC_CHANGE_MGMT.md`, `future_implementation_plan.md`). | Create a single “Change Management” hub and reference it via markdown includes. |
| README | Missing quick‑start badges. | Add shields.io badges for Docker Hub, GitHub Actions, and PyPI. |

## 5️⃣ Security & Hygiene

| Area | Issue | Suggested Fix |
|------|-------|---------------|
| Secrets | `.env.example` present; risk of committing real `.env`. | Add pre‑commit hook to block committing files matching `*.env*` unless named `.env.example`. |
| Dependency Updates | Some libraries not version‑pinned (`urllib3`, `flask`). | Run `pip‑compile --generate-hashes` and `npm audit fix` to lock safe versions. |
| Static Analysis | No SAST step in CI. | Add `bandit` for Python and `npm audit` for Node to the workflow. |

## 6️⃣ Performance Hotspots (Profiling)

1. **Log Rotation** (`tests/test_log_rotation.py`) – uses a naïve `while True` loop. → Replace with `watchdog` observer to react to file changes.
2. **Gateway Health Check** (`tests/test_gateway_health_check.py`) – makes real HTTP calls in unit tests. → Mock external calls with `responses` library.
3. **Agent Integration** (`tests/test_agent_integration.py`) – spins up Docker containers per test. → Use `pytest‑docker` fixture to start container once per session.

## 📍 Prioritized Quick‑Win Action Plan

1. **Run `autoflake`** to clean dead imports/functions and commit the changes.
2. **Add `.gitignore` entry** for `graphify-out/cache/` and purge existing cached JSON files.
3. **Centralize logging** – create `logging_config.py` and replace all `basicConfig` calls.
4. **Refactor Dockerfile** – split into builder and runtime stages.
5. **Introduce CI caching** for pip and npm to reduce workflow runtime (~30 %).

Implementing these steps will shrink the repository, speed up CI, improve runtime performance, and make the codebase easier to maintain.
