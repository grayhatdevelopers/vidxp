import assert from 'node:assert/strict';
import { test } from 'node:test';

import { summarizeResults } from './report.mjs';

test('summarizes comparison metrics by benchmark condition', () => {
  const summaries = summarizeResults([
    {
      condition: 'vidxp-on', success: true, iou: 0.75,
      recall03: 1, recall05: 1, recall07: 1,
      latencyMs: 75_000, totalTokens: 300_000, cachedTokens: 250_000,
      completionTokens: 2_000, cost: 0.8, mcpCalls: 6,
      mediaShellCalls: 0, skillLoads: 1,
    },
    {
      condition: 'vidxp-off', success: true, iou: 0.88,
      recall03: 1, recall05: 1, recall07: 1,
      latencyMs: 112_000, totalTokens: 330_000, cachedTokens: 290_000,
      completionTokens: 3_600, cost: 0.81, mcpCalls: 0,
      mediaShellCalls: 10, skillLoads: 0,
    },
  ]);

  assert.deepEqual(summaries.map((summary) => summary.condition), ['vidxp-on', 'vidxp-off']);
  assert.equal(summaries[0].meanIou, 0.75);
  assert.equal(summaries[0].totalTokens, 300_000);
  assert.equal(summaries[0].mcpCalls, 6);
  assert.equal(summaries[1].meanLatencyMs, 112_000);
  assert.equal(summaries[1].mediaShellCalls, 10);
});
