import assert from 'node:assert/strict';
import { test } from 'node:test';

import { summarizeResults, summarizeRetrieval } from './report.mjs';

test('summarizes comparison metrics by benchmark condition', () => {
  const summaries = summarizeResults([
    {
      condition: 'vidxp-on', success: true, iou: 0.75,
      chunkHit: 1, eventCoverage: 1, durationInRange: 1,
      recall03: 1, recall05: 1, recall07: 1,
      expectedStart: 0, expectedEnd: 6, predictedStart: 0, predictedEnd: 8,
      latencyMs: 75_000, totalTokens: 300_000, promptTokens: 298_000,
      cachedTokens: 250_000, completionTokens: 2_000, reasoningTokens: 600,
      requests: 1, cost: 0.8, agentItems: 9, toolCalls: 7, mcpCalls: 6,
      shellCalls: 1, mediaShellCalls: 0, skillLoads: 1,
    },
    {
      condition: 'vidxp-off', success: true, iou: 0.88,
      chunkHit: 1, eventCoverage: 1, durationInRange: 1,
      recall03: 1, recall05: 1, recall07: 1,
      expectedStart: 0, expectedEnd: 6, predictedStart: 0, predictedEnd: 6.8,
      latencyMs: 112_000, totalTokens: 330_000, promptTokens: 326_400,
      cachedTokens: 290_000, completionTokens: 3_600, reasoningTokens: 1_400,
      requests: 1, cost: 0.81, agentItems: 12, toolCalls: 10, mcpCalls: 0,
      shellCalls: 10, mediaShellCalls: 10, skillLoads: 0,
    },
    {
      condition: 'model-only', success: true, iou: 0.9,
      chunkHit: 1, eventCoverage: 1, durationInRange: 1,
      recall03: 1, recall05: 1, recall07: 1,
      expectedStart: 0, expectedEnd: 6, predictedStart: 0, predictedEnd: 6.5,
      latencyMs: 80_000, totalTokens: 310_000, promptTokens: 307_000,
      cachedTokens: 270_000, completionTokens: 3_000, reasoningTokens: 1_000,
      requests: 1, cost: 0.7, agentItems: 11, toolCalls: 9, mcpCalls: 5,
      shellCalls: 4, mediaShellCalls: 3, skillLoads: 1,
    },
  ]);

  assert.deepEqual(
    summaries.map((summary) => summary.condition),
    ['vidxp-on', 'vidxp-off', 'model-only'],
  );
  assert.equal(summaries[0].meanIou, 0.75);
  assert.equal(summaries[0].chunkHits, 1);
  assert.equal(summaries[0].chunkScored, 1);
  assert.equal(summaries[0].chunkHitRate, 1);
  assert.equal(summaries[0].meanEventCoverage, 1);
  assert.equal(summaries[0].totalTokens, 300_000);
  assert.equal(summaries[0].promptTokens, 298_000);
  assert.equal(summaries[0].uncachedPromptTokens, 48_000);
  assert.equal(summaries[0].reasoningTokens, 600);
  assert.equal(summaries[0].meanEndError, 2);
  assert.equal(summaries[0].meanDurationError, 2);
  assert.equal(summaries[0].toolCalls, 7);
  assert.equal(summaries[0].mcpCalls, 6);
  assert.equal(summaries[1].meanLatencyMs, 112_000);
  assert.equal(summaries[1].mediaShellCalls, 10);
  assert.equal(summaries[2].mcpCalls, 5);
});

test('reports fused and per-modality retrieval boundary quality', () => {
  const summary = summarizeRetrieval(
    { task: 'opening', expectedStart: 0, expectedEnd: 6 },
    {
      moments: [
        {
          rank: 1,
          start: 0,
          end: 8,
          hits: [
            { modality: 'action', rank: 1, start: 0, end: 8 },
            { modality: 'scene', rank: 1, start: 1, end: 2 },
            { modality: 'scene', rank: 2, start: 1, end: 4 },
          ],
        },
        { rank: 2, start: 20, end: 30, hits: [] },
        { rank: 3, start: 0, end: 6, hits: [] },
      ],
    },
  );

  assert.equal(summary.topMomentIou, 0.75);
  assert.equal(summary.bestByModality.get('action').iou, 0.75);
  assert.equal(summary.bestByModality.get('scene').rank, 2);
  assert.equal(summary.bestByModality.get('scene').fusedRank, 1);
  assert.equal(summary.bestByModality.get('scene').iou, 0.5);
  assert.deepEqual(summary.momentIous, [0.75, 0, 1]);
});
