import assert from 'node:assert/strict';
import { after, test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';

const server = await createServer({
  server: { middlewareMode: true }, appType: 'custom', optimizeDeps: { noDiscovery: true },
});
after(() => server.close());
const { WorkflowReviewCard } = await server.ssrLoadModule('/src/components/ProposalsPanel.tsx');
const common = { id: 1, status: 'pending', severity: 'warning', created: 1 };
const render = review => renderToStaticMarkup(React.createElement(WorkflowReviewCard, {
  review: { ...common, ...review }, onStatus: () => {},
}));

test('renders a job review with its evidence and recommendations', () => {
  const html = render({ run_id: 7, job_id: 'job-x', summary: 'Repeated fetch',
    evidence: ['same result', { summary: 'slow call' }], recommendations: ['Narrow input'] });
  assert.match(html, /Repeated fetch/);
  assert.match(html, /Evidence \(2\) and recommendations \(1\)/);
  assert.match(html, /slow call/);
  assert.match(html, /Narrow input/);
  assert.match(html, /Run #7/);
});

test('renders a chat tool review without job-only fields', () => {
  const html = render({ run_id: null, job_id: '', event_id: 23, tool: 'fetch',
    findings: ["Tool 'fetch' failed."] });
  assert.match(html, /Tool review: fetch/);
  assert.match(html, /Chat tool event #23/);
  assert.match(html, /Findings \(1\)/);
  assert.match(html, /failed/);
  assert.match(html, /Acknowledge/);
  assert.match(html, /Dismiss/);
});

test('reports an incomplete review instead of crashing', () => {
  const html = render({ run_id: null, job_id: '' });
  assert.match(html, /incomplete details/);
});
