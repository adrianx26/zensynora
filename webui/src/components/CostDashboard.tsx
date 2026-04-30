/**
 * CostDashboard — visualizes LLM spend across providers, models, and time.
 *
 * Self-contained: no external chart libraries (an inline SVG sparkline keeps
 * the bundle small and avoids a dependency churn). Drop into App.tsx with:
 *
 *     import CostDashboard from './components/CostDashboard';
 *     ...
 *     <CostDashboard apiBase={getApiBase()} apiKey={apiKey} />
 *
 * The component fetches /api/v1/costs/* on mount and refreshes every 60s.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';

interface SummaryRow {
  total_cost_usd: number;
  total_prompt_tokens: number;
  total_completion_tokens: number;
  tracked_months: string[];
}

interface ProviderRow {
  provider: string;
  total_cost: number;
  total_prompt: number;
  total_completion: number;
  request_count: number;
}

interface ModelRow extends ProviderRow {
  model: string;
}

interface TimelineRow {
  day: string;
  total_cost: number;
  total_tokens: number;
  request_count: number;
}

interface Props {
  apiBase: string;
  apiKey?: string;
  /** Polling interval in seconds. 0 disables auto-refresh. */
  refreshSeconds?: number;
}

function formatUSD(value: number): string {
  if (!value) return '$0.00';
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

function formatInt(value: number): string {
  return new Intl.NumberFormat().format(value || 0);
}

const CostDashboard: React.FC<Props> = ({ apiBase, apiKey, refreshSeconds = 60 }) => {
  const [summary, setSummary] = useState<SummaryRow | null>(null);
  const [providers, setProviders] = useState<ProviderRow[]>([]);
  const [models, setModels] = useState<ModelRow[]>([]);
  const [timeline, setTimeline] = useState<TimelineRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const headers = useMemo(() => {
    const h: Record<string, string> = { 'Content-Type': 'application/json' };
    if (apiKey) h['X-API-Key'] = apiKey;
    return h;
  }, [apiKey]);

  const fetchAll = useCallback(async () => {
    try {
      const [s, p, m, t] = await Promise.all([
        fetch(`${apiBase}/api/v1/costs/summary`, { headers }),
        fetch(`${apiBase}/api/v1/costs/by-provider`, { headers }),
        fetch(`${apiBase}/api/v1/costs/by-model?limit=10`, { headers }),
        fetch(`${apiBase}/api/v1/costs/timeline?days=30`, { headers }),
      ]);
      if (!s.ok || !p.ok || !m.ok || !t.ok) {
        throw new Error(`HTTP ${[s, p, m, t].find(r => !r.ok)?.status}`);
      }
      const [sJson, pJson, mJson, tJson] = await Promise.all([s.json(), p.json(), m.json(), t.json()]);
      setSummary(sJson);
      setProviders(pJson.rows || []);
      setModels(mJson.rows || []);
      setTimeline(tJson.rows || []);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, [apiBase, headers]);

  useEffect(() => {
    fetchAll();
    if (!refreshSeconds) return;
    const iv = setInterval(fetchAll, refreshSeconds * 1000);
    return () => clearInterval(iv);
  }, [fetchAll, refreshSeconds]);

  // Inline SVG sparkline. Fewer deps, fewer surprises.
  const Sparkline: React.FC<{ rows: TimelineRow[] }> = ({ rows }) => {
    if (!rows.length) return <span style={{ color: 'var(--muted, #888)' }}>No activity in the last 30 days</span>;
    const max = Math.max(...rows.map(r => r.total_cost), 0.0001);
    const w = 360;
    const h = 60;
    const step = rows.length > 1 ? w / (rows.length - 1) : 0;
    const points = rows
      .map((r, i) => `${i * step},${h - (r.total_cost / max) * (h - 4) - 2}`)
      .join(' ');
    return (
      <svg width={w} height={h} role="img" aria-label="Daily spend (last 30 days)">
        <polyline
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          points={points}
        />
      </svg>
    );
  };

  if (loading) return <div className="cost-dashboard">Loading cost data…</div>;
  if (error) return <div className="cost-dashboard cost-error">Failed to load costs: {error}</div>;

  return (
    <div className="cost-dashboard">
      <h2>LLM Cost Dashboard</h2>

      <section className="cost-summary">
        <div className="cost-card">
          <div className="cost-label">Total spend (all time)</div>
          <div className="cost-value">{formatUSD(summary?.total_cost_usd || 0)}</div>
        </div>
        <div className="cost-card">
          <div className="cost-label">Prompt tokens</div>
          <div className="cost-value">{formatInt(summary?.total_prompt_tokens || 0)}</div>
        </div>
        <div className="cost-card">
          <div className="cost-label">Completion tokens</div>
          <div className="cost-value">{formatInt(summary?.total_completion_tokens || 0)}</div>
        </div>
        <div className="cost-card">
          <div className="cost-label">Months tracked</div>
          <div className="cost-value">{summary?.tracked_months?.length ?? 0}</div>
        </div>
      </section>

      <section className="cost-section">
        <h3>Last 30 days</h3>
        <Sparkline rows={timeline} />
      </section>

      <section className="cost-section">
        <h3>By provider (this month)</h3>
        <table className="cost-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Cost</th>
              <th>Prompt tokens</th>
              <th>Completion tokens</th>
              <th>Requests</th>
            </tr>
          </thead>
          <tbody>
            {providers.length === 0 ? (
              <tr><td colSpan={5} style={{ color: 'var(--muted, #888)' }}>No spend recorded this month.</td></tr>
            ) : providers.map(row => (
              <tr key={row.provider}>
                <td>{row.provider}</td>
                <td>{formatUSD(row.total_cost)}</td>
                <td>{formatInt(row.total_prompt)}</td>
                <td>{formatInt(row.total_completion)}</td>
                <td>{formatInt(row.request_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="cost-section">
        <h3>Top models (this month)</h3>
        <table className="cost-table">
          <thead>
            <tr>
              <th>Provider</th>
              <th>Model</th>
              <th>Cost</th>
              <th>Requests</th>
            </tr>
          </thead>
          <tbody>
            {models.length === 0 ? (
              <tr><td colSpan={4} style={{ color: 'var(--muted, #888)' }}>No model usage recorded.</td></tr>
            ) : models.map(row => (
              <tr key={`${row.provider}/${row.model}`}>
                <td>{row.provider}</td>
                <td>{row.model}</td>
                <td>{formatUSD(row.total_cost)}</td>
                <td>{formatInt(row.request_count)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
};

export default CostDashboard;
