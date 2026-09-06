import assert from 'node:assert/strict';
import { test } from 'node:test';

import { sanitizePromptfooExport } from './export-eval.mjs';
import {
  assertionReason,
  summarizePrimaryPairs,
  summarizeRecordedItems,
  summarizeResults,
  summarizeRetrieval,
  summarizeSurfaceRecall,
  summarizeSurfaceTransfer,
} from './report.mjs';

test('sanitizes a Promptfoo export without removing its audit data', () => {
  const sanitized = sanitizePromptfooExport({
    metadata: { promptfooVersion: '0.122.2' },
    config: { apiKey: 'secret', workingDir: '/Users/test/repo/workspace' },
    results: {
      results: [{
        prompt: { raw: 'Find the event.' },
        response: { raw: 'large command output', sessionId: 'session-1', output: '{}' },
      }],
    },
    traces: [{
      spans: [{
        attributes: {
          command: '/Users/test/tool --version; inspect /Users/t…/truncated',
        },
      }],
    }],
  }, {
    repoRoot: '/Users/test/repo',
    userHome: '/Users/test',
    machineId: 'mac-fixture-01',
  });

  assert.equal(sanitized.config.apiKey, '<REDACTED>');
  assert.equal(sanitized.config.workingDir, '<REPO>/workspace');
  assert.equal(sanitized.results.results[0].prompt.raw, 'Find the event.');
  assert.equal(sanitized.results.results[0].response.output, '{}');
  assert.equal('raw' in sanitized.results.results[0].response, false);
  assert.equal('sessionId' in sanitized.results.results[0].response, false);
  assert.equal(
    sanitized.traces[0].spans[0].attributes.command,
    '<HOME>/tool --version; inspect <HOME>/truncated',
  );
  assert.doesNotMatch(JSON.stringify(sanitized), /\btest\b/);
  assert.equal(sanitized.metadata.vidxpExport.sanitized, true);
  assert.equal(sanitized.metadata.vidxpExport.machineId, 'mac-fixture-01');
});

test('summarizes comparison metrics by benchmark condition', () => {
  const summaries = summarizeResults([
    {
      condition: 'vidxp-on', success: true, iou: 0.75,
      integrityPassed: true,
      chunkHit: 1, eventCoverage: 1, durationInRange: 1,
      recall03: 1, recall05: 1, recall07: 1,
      expectedStart: 0, expectedEnd: 6, predictedStart: 0, predictedEnd: 8,
      latencyMs: 75_000, totalTokens: 300_000, promptTokens: 298_000,
      cachedTokens: 250_000, completionTokens: 2_000, reasoningTokens: 600,
      requests: 1, cost: 0.8, agentItems: 9, toolCalls: 7, mcpCalls: 6,
      shellCalls: 1, skillLoads: 1,
    },
    {
      condition: 'vidxp-off', success: true, iou: 0.88,
      integrityPassed: true,
      chunkHit: 1, eventCoverage: 1, durationInRange: 1,
      recall03: 1, recall05: 1, recall07: 1,
      expectedStart: 0, expectedEnd: 6, predictedStart: 0, predictedEnd: 6.8,
      latencyMs: 112_000, totalTokens: 330_000, promptTokens: 326_400,
      cachedTokens: 290_000, completionTokens: 3_600, reasoningTokens: 1_400,
      requests: 1, cost: 0.81, agentItems: 12, toolCalls: 10, mcpCalls: 0,
      shellCalls: 10, skillLoads: 0,
    },
    {
      condition: 'clean-user', success: true, iou: 0.9,
      integrityPassed: true,
      chunkHit: 1, eventCoverage: 1, durationInRange: 1,
      recall03: 1, recall05: 1, recall07: 1,
      expectedStart: 0, expectedEnd: 6, predictedStart: 0, predictedEnd: 6.5,
      latencyMs: 80_000, totalTokens: 310_000, promptTokens: 307_000,
      cachedTokens: 270_000, completionTokens: 3_000, reasoningTokens: 1_000,
      requests: 1, cost: 0.7, agentItems: 11, toolCalls: 9, mcpCalls: 5,
      shellCalls: 4, skillLoads: 1,
    },
  ]);

  assert.deepEqual(
    summaries.map((summary) => summary.condition),
    ['vidxp-on', 'vidxp-off', 'clean-user'],
  );
  assert.equal(summaries[0].meanIou, 0.75);
  assert.equal(summaries[0].chunkHits, 1);
  assert.equal(summaries[0].chunkScored, 1);
  assert.equal(summaries[0].meanTotalTokens, 300_000);
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
  assert.equal(summaries[2].mcpCalls, 5);
});

test('keeps top-one and top-three candidate quality separate', () => {
  const [summary] = summarizeResults([
    {
      condition: 'vidxp-on', integrityPassed: true,
      chunkHit: 1, top1ChunkHit: 0, chunkMrr: 0.5, candidateCount: 2,
      eventCoverage: 1, durationInRange: 1,
      iou: 0, bestIou: 0.6,
      recall03: 0, recall05: 0, recall07: 0,
      recallAt3_03: 1, recallAt3_05: 1, recallAt3_07: 0,
    },
  ]);

  assert.equal(summary.chunkHitRate, 1);
  assert.equal(summary.top1ChunkHitRate, 0);
  assert.equal(summary.meanChunkMrr, 0.5);
  assert.equal(summary.meanCandidateCount, 2);
  assert.equal(summary.meanIou, 0);
  assert.equal(summary.meanBestIou, 0.6);
  assert.equal(summary.recallAt3_05, 1);
});

test('uses only matched integrity-valid primary pairs for the product comparison', () => {
  const paired = summarizePrimaryPairs([
    {
      task: 'one', repetition: 1, condition: 'vidxp-on', integrityPassed: true,
      chunkHit: 1, totalTokens: 100,
    },
    {
      task: 'one', repetition: 1, condition: 'vidxp-off', integrityPassed: true,
      chunkHit: 1, totalTokens: 200,
    },
    {
      task: 'two', repetition: 1, condition: 'vidxp-on', integrityPassed: false,
      chunkHit: 1, totalTokens: 100,
    },
    {
      task: 'two', repetition: 1, condition: 'vidxp-off', integrityPassed: true,
      chunkHit: 0, totalTokens: 200,
    },
  ]);

  assert.equal(paired.totalPairs, 2);
  assert.equal(paired.validPairs, 1);
  assert.equal(paired.pairs.length, 1);
  assert.deepEqual(paired.results.map((result) => result.task), ['one', 'one']);
});

test('counts Promptfoo recorded items without parsing command text', () => {
  assert.deepEqual(summarizeRecordedItems(JSON.stringify({
    items: [
      { type: 'command_execution', command: '"$MEDIA_TOOL" -i video.mp4' },
      { type: 'mcp_tool_call', server: 'vidxp', tool: 'search_moments' },
      { type: 'file_change' },
      { type: 'agent_message' },
    ],
  })), {
    agentItems: 4,
    toolCalls: 2,
    mcpCalls: 1,
    shellCalls: 1,
  });
});

test('reports fused and per-modality retrieval boundary quality', () => {
  const summary = summarizeRetrieval(
    {
      task: 'opening', expectedStart: 0, expectedEnd: 6,
      testVars: { target_chunk_seconds: 10, min_event_coverage: 0.5 },
    },
    {
      surface_candidates: [
        { rank: 1, start: 20, end: 30, state: 'ready' },
        { rank: 2, start: 0, end: 10, state: 'ready' },
        { rank: 3, start: 40, end: 50, state: 'failed' },
      ],
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
  assert.deepEqual(summary.surfaceCoverages, [0, 1]);
  assert.deepEqual(summarizeSurfaceRecall([summary], 1), {
    hits: 0,
    scored: 1,
    rate: 0,
    meanBestCoverage: 0,
  });
  assert.deepEqual(summarizeSurfaceRecall([summary], 3), {
    hits: 1,
    scored: 1,
    rate: 1,
    meanBestCoverage: 1,
  });
  assert.deepEqual(summarizeSurfaceTransfer([
    { ...summary, finalChunkHit: 0 },
  ], 3), {
    surfacedAndReturned: 0,
    surfacedOnly: 1,
    returnedOnly: 0,
    neither: 0,
  });
});

test('extracts the reason for the requested Promptfoo assertion', () => {
  const grading = {
    reason: 'Combined failure summary',
    componentResults: [
      { reason: 'Temporal miss', assertion: { metric: 'temporal_grounding' } },
      { reason: 'Isolation failure', assertion: { metric: 'ablation_boundary' } },
    ],
  };

  assert.equal(assertionReason(grading, 'ablation_boundary'), 'Isolation failure');
});
