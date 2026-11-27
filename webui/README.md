# ZenSynora Web UI

> **Current release: v0.8.5 · Updated 2026-11-27.** Colored maintenance
> progress, optional update decisions and confirmed Doctor GitHub reinstall.
> User guide: [docs/webui_guide.md](../docs/webui_guide.md).

React 19 + TypeScript + Vite front end for ZenSynora. The production build in
`dist/` is served by the Python backend (`myclaw/web/api.py`) on the same
port, so you normally never run this folder yourself:

```bash
zensynora webui            # http://localhost:8000
```

## Pages

| Page | Component | Backend |
|---|---|---|
| Chat — channels, direct messages, @mentions, agent-to-agent activity | `src/components/ChatView.tsx` (+ sidebar in `src/App.tsx`) | `/api/channels…`, WebSocket `/ws/channels/{id}`, `/api/activity` |
| Models — default model, rotation, your own providers | `src/components/ModelsView.tsx` | `/api/models`, `/api/models/default`, `/api/models/rotate`, `/api/settings` |
| Jobs — add, run, monitor, evaluate, benchmark, opt-in insights and reviewed Medic report export | `src/components/JobsView.tsx` | `/api/jobs…` |
| Backups — create, check, restore, automatic, detailed progress | `src/components/BackupsView.tsx`, `src/components/ProcessProgress.tsx` | `/api/backups…`, `GET /api/backups/process` |
| Updates — detailed step/rollback outcomes, optional Continue/Skip/Abort, restart | `src/components/UpdatesView.tsx`, `src/components/ProcessProgress.tsx` | `/api/update/status`, `/api/update/start`, `/api/update/decision` |
| Settings — providers, agents (✨ generate prompt), channels, features, security | `src/components/SettingsView.tsx` | `GET/PUT /api/settings`, `POST /api/settings/probe`, `POST /api/agents/generate-prompt` |
| Customize — profiles (style, structure, behaviour, tools off), who uses what, Telegram answering agent / team, agents' proposals, AI review / adapt, built-in features | `src/components/CustomizeView.tsx` (Built-in features: `SettingsView only="Features"`) | `/api/customize…`, `PUT /api/settings` (Telegram) |
| Doctor — checks, reports, logs, confirmed GitHub reinstall, proposals | `src/components/DoctorView.tsx`, `src/components/UpdatesView.tsx`, `src/components/ProposalsPanel.tsx` | `/api/system/doctor…`, `/api/medic/…`, `POST /api/doctor/reinstall` |
| Costs & usage | `src/components/CostDashboard.tsx` | `/api/v1/costs/*` |
| Tools | `src/components/ToolsView.tsx` | `GET /api/tools` |
| Sidebar health | `src/App.tsx` | `GET /api/system/health` (every 15 s) |

Chat events on `/ws/channels/{id}` (JSON): `hello`, `message`, `stream_start`,
`chunk`, `stream_end`, `turn_start`, `turn_end`, `tool_start`, `tool_end`,
`delegation_start`, `delegation_end`, `job_run`, `channel`, `error`. Send
`{"type": "message", "text": …}`, `{"type": "stop"}` or, with two-factor
authentication on, `{"type": "mfa", "code": …}` first. The older per-agent
socket `/ws/chat/{agent}` still works for scripts.

Settings, Models, Jobs, Backups, Doctor, Costs, deleting channels and history
clearing are answered only for requests from the same computer, or with the admin API key (`X-API-Key`; the UI asks
for it when needed). Secrets are never sent to the browser.

Maintenance colors use the root CSS success/danger/accent variables and the
existing amber warning color, with text labels as well as color. Backup/restore
parts are selected before starting; only explicitly optional updater steps
can pause. User-data backup, verification and restore recovery cannot be skipped.
Doctor reinstall and optional-step decisions require the guarded UI header.

## Development

```bash
cd webui
npm ci
npm run dev        # http://localhost:5173, talks to the backend on :8000
npm run lint
npm run build      # writes dist/ — installs and updates do this for you
```

Start the backend separately (`zensynora webui`). In dev mode the UI detects
port 5173 and sends API/WebSocket calls to port 8000 (`src/api.ts`).

## Conventions

* Colours come from CSS variables in `src/index.css` (dark and light theme).
* New pages: add a component in `src/components/`, a `View` id and a
  `NavButton` in `App.tsx`, and backend routes in `myclaw/web/settings_api.py`
  behind the `guard` dependency when they expose settings or data.
* Keep the bundle dependency-free beyond React (charts are inline SVG).
