# ZenSynora Web UI

> Replaces the Vite template README (2026-10-13, v0.7.0).

React 19 + TypeScript + Vite front end for ZenSynora. The production build in
`dist/` is served by the Python backend (`myclaw/web/api.py`) on the same
port, so you normally never run this folder yourself:

```bash
zensynora webui            # http://localhost:8000
```

## Pages

| Page | Component | Backend |
|---|---|---|
| Chat (streaming, per-agent history) | `src/App.tsx` | WebSocket `/ws/chat/{agent}` |
| Settings — model, providers, agents, channels, features, security | `src/components/SettingsView.tsx` | `GET/PUT /api/settings`, `POST /api/settings/probe` |
| Doctor — health checks and safe fixes | `src/components/DoctorView.tsx` | `GET /api/system/doctor`, `POST /api/system/doctor/fix` |
| Costs & usage | `src/components/CostDashboard.tsx` | `/api/v1/costs/*` |
| Tools | `src/components/ToolsView.tsx` | `GET /api/tools` |
| Sidebar health | `src/App.tsx` | `GET /api/system/health` (every 15 s) |

Settings, Doctor, Costs and history clearing are answered only for requests
from the same computer, or with the admin API key (`X-API-Key`; the UI asks
for it when needed). Secrets are never sent to the browser.

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
