import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { spawnSync } from 'node:child_process';

function parseJson(value, fallback = {}) {
  if (typeof value !== 'string') {
    return fallback;
  }
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function mean(values) {
  const numbers = values.filter((value) => Number.isFinite(value));
  return numbers.length === 0
    ? null
    : numbers.reduce((total, value) => total + value, 0) / numbers.length;
}

function sum(values) {
  return values
    .filter((value) => Number.isFinite(value))
    .reduce((total, value) => total + value, 0);
}

function sumOrNull(values) {
  const numbers = values.filter((value) => Number.isFinite(value));
  return numbers.length === 0
    ? null
    : numbers.reduce((total, value) => total + value, 0);
}

function fixed(value, digits = 3) {
  return Number.isFinite(value) ? value.toFixed(digits) : 'n/a';
}

function seconds(milliseconds) {
  return Number.isFinite(milliseconds) ? `${(milliseconds / 1000).toFixed(3)}s` : 'n/a';
}

function secondsValue(value) {
  return Number.isFinite(value) ? `${value.toFixed(3)}s` : 'n/a';
}

function integer(value) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString('en-US') : 'n/a';
}

function money(value, digits = 6) {
  return Number.isFinite(value) ? `$${value.toFixed(digits)}` : 'n/a';
}

function tokenDifference(total, cached) {
  return Number.isFinite(total) && Number.isFinite(cached)
    ? Math.max(0, total - cached)
    : null;
}

function boundaryError(predicted, expected) {
  return Number.isFinite(predicted) && Number.isFinite(expected)
    ? predicted - expected
    : null;
}

function absolute(value) {
  return Number.isFinite(value) ? Math.abs(value) : null;
}

function durationError(result) {
  if (
    !Number.isFinite(result.predictedStart)
    || !Number.isFinite(result.predictedEnd)
    || !Number.isFinite(result.expectedStart)
    || !Number.isFinite(result.expectedEnd)
  ) {
    return null;
  }
  return (result.predictedEnd - result.predictedStart)
    - (result.expectedEnd - result.expectedStart);
}

function interval(start, end) {
  return Number.isFinite(start) && Number.isFinite(end)
    ? `${start.toFixed(3)}–${end.toFixed(3)}s`
    : 'n/a';
}

function intervalIou(start, end, expectedStart, expectedEnd) {
  if (![start, end, expectedStart, expectedEnd].every(Number.isFinite)) {
    return null;
  }
  const intersection = Math.max(0, Math.min(end, expectedEnd) - Math.max(start, expectedStart));
  const union = Math.max(end, expectedEnd) - Math.min(start, expectedStart);
  return union > 0 ? intersection / union : 0;
}

function signed(value, digits = 3) {
  if (!Number.isFinite(value)) {
    return 'n/a';
  }
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}`;
}

function signedMoney(value) {
  if (!Number.isFinite(value)) {
    return 'n/a';
  }
  return `${value >= 0 ? '+' : '-'}$${Math.abs(value).toFixed(6)}`;
}

function signedSeconds(value) {
  return Number.isFinite(value) ? `${signed(value)}s` : 'n/a';
}

export function summarizeResults(results) {
  return ['vidxp-on', 'vidxp-off'].map((condition) => {
    const selected = results.filter((result) => result.condition === condition);
    return {
      condition,
      runs: selected.length,
      passed: selected.filter((result) => result.success).length,
      meanIou: mean(selected.map((result) => result.iou)),
      recall03: mean(selected.map((result) => result.recall03)),
      recall05: mean(selected.map((result) => result.recall05)),
      recall07: mean(selected.map((result) => result.recall07)),
      meanStartError: mean(selected.map((result) => (
        absolute(boundaryError(result.predictedStart, result.expectedStart))
      ))),
      meanEndError: mean(selected.map((result) => (
        absolute(boundaryError(result.predictedEnd, result.expectedEnd))
      ))),
      meanDurationError: mean(selected.map((result) => absolute(durationError(result)))),
      meanLatencyMs: mean(selected.map((result) => result.latencyMs)),
      totalLatencyMs: sum(selected.map((result) => result.latencyMs)),
      totalTokens: sumOrNull(selected.map((result) => result.totalTokens)),
      promptTokens: sumOrNull(selected.map((result) => result.promptTokens)),
      uncachedPromptTokens: sumOrNull(selected.map((result) => (
        tokenDifference(result.promptTokens, result.cachedTokens)
      ))),
      cachedTokens: sumOrNull(selected.map((result) => result.cachedTokens)),
      completionTokens: sumOrNull(selected.map((result) => result.completionTokens)),
      reasoningTokens: sumOrNull(selected.map((result) => result.reasoningTokens)),
      requests: sumOrNull(selected.map((result) => result.requests)),
      cost: sumOrNull(selected.map((result) => result.cost)),
      agentItems: sum(selected.map((result) => result.agentItems)),
      toolCalls: sum(selected.map((result) => result.toolCalls)),
      mcpCalls: sum(selected.map((result) => result.mcpCalls)),
      shellCalls: sum(selected.map((result) => result.shellCalls)),
      mediaShellCalls: sum(selected.map((result) => result.mediaShellCalls)),
      skillLoads: sum(selected.map((result) => result.skillLoads)),
    };
  }).filter((summary) => summary.runs > 0);
}

export function loadLatestEvaluation() {
  const configDirectory = process.env.PROMPTFOO_CONFIG_DIR || join(homedir(), '.promptfoo');
  const databasePath = join(configDirectory, 'promptfoo.db');
  const database = new DatabaseSync(databasePath, { readOnly: true });
  try {
    const evaluation = database.prepare(
      'SELECT id, created_at, description FROM evals ORDER BY created_at DESC LIMIT 1',
    ).get();
    if (!evaluation) {
      throw new Error('Promptfoo has no saved evaluation.');
    }
    const rows = database.prepare(`
      SELECT id, test_idx, test_case, response, success, score, latency_ms, cost,
             error, grading_result, named_scores
      FROM eval_results
      WHERE eval_id = ?
      ORDER BY test_idx, id
    `).all(evaluation.id);
    const traceRows = database.prepare(`
      SELECT trace_id, metadata
      FROM traces
      WHERE evaluation_id = ?
    `).all(evaluation.id);
    const spansForTrace = database.prepare(`
      SELECT name, start_time, end_time, attributes
      FROM spans
      WHERE trace_id = ?
      ORDER BY start_time
    `);
    const traceStats = new Map();
    let firstSpan = null;
    let lastSpan = null;
    for (const trace of traceRows) {
      const metadata = parseJson(trace.metadata);
      const spans = spansForTrace.all(trace.trace_id);
      const itemIds = new Set();
      let agentItems = 0;
      let toolCalls = 0;
      let mcpCalls = 0;
      let shellCalls = 0;
      let mediaShellCalls = 0;
      for (const span of spans) {
        const attributes = parseJson(span.attributes);
        const itemId = attributes['codex.item.id'];
        const itemType = attributes['codex.item.type'];
        if (typeof itemId === 'string' && !itemIds.has(itemId)) {
          itemIds.add(itemId);
          agentItems += 1;
          if (itemType === 'command_execution') {
            shellCalls += 1;
            toolCalls += 1;
          } else if (typeof itemType === 'string' && itemType.endsWith('_tool_call')) {
            toolCalls += 1;
          }
          if (itemType === 'mcp_tool_call' && attributes['codex.mcp.server'] === 'vidxp') {
            mcpCalls += 1;
          }
        }
        const command = attributes['codex.command'];
        if (
          typeof command === 'string'
          && /(?:^|[\s'"/\\])ff(?:mpeg|probe)(?:\s|$)/i.test(command)
        ) {
          mediaShellCalls += 1;
        }
        if (Number.isFinite(span.start_time)) {
          firstSpan = firstSpan === null ? span.start_time : Math.min(firstSpan, span.start_time);
        }
        if (Number.isFinite(span.end_time)) {
          lastSpan = lastSpan === null ? span.end_time : Math.max(lastSpan, span.end_time);
        }
      }
      traceStats.set(metadata.testIdx, {
        agentItems,
        toolCalls,
        mcpCalls,
        shellCalls,
        mediaShellCalls,
      });
    }

    const results = rows.map((row) => {
      const testCase = parseJson(row.test_case);
      const response = parseJson(row.response);
      const output = parseJson(response.output);
      const namedScores = parseJson(row.named_scores);
      const responseMetadata = response.metadata || {};
      const stats = traceStats.get(row.test_idx) || {};
      return {
        task: testCase.metadata?.task_id || testCase.vars?.id || String(row.test_idx),
        condition: testCase.vars?.condition || 'unknown',
        success: row.success === 1,
        reason: parseJson(row.grading_result).reason || row.error || '',
        expectedStart: testCase.vars?.expected_start,
        expectedEnd: testCase.vars?.expected_end,
        predictedStart: output.start_seconds,
        predictedEnd: output.end_seconds,
        answer: output.answer,
        modalities: Array.isArray(output.modalities) ? output.modalities : [],
        sourceJobId: output.source_job_id,
        evidenceCount: Array.isArray(output.evidence) ? output.evidence.length : 0,
        iou: Number.isFinite(namedScores.temporal_iou) ? namedScores.temporal_iou : 0,
        recall03: Number.isFinite(namedScores.r1_tiou_0_3)
          ? namedScores.r1_tiou_0_3
          : 0,
        recall05: Number.isFinite(namedScores.r1_tiou_0_5)
          ? namedScores.r1_tiou_0_5
          : 0,
        recall07: Number.isFinite(namedScores.r1_tiou_0_7)
          ? namedScores.r1_tiou_0_7
          : 0,
        latencyMs: row.latency_ms,
        totalTokens: response.tokenUsage?.total,
        promptTokens: response.tokenUsage?.prompt,
        cachedTokens: response.tokenUsage?.cached,
        completionTokens: response.tokenUsage?.completion,
        reasoningTokens: response.tokenUsage?.completionDetails?.reasoning,
        requests: response.tokenUsage?.numRequests,
        cost: row.cost,
        agentItems: stats.agentItems || 0,
        toolCalls: stats.toolCalls || 0,
        mcpCalls: stats.mcpCalls || 0,
        shellCalls: stats.shellCalls || 0,
        mediaShellCalls: stats.mediaShellCalls || 0,
        skillLoads: Array.isArray(responseMetadata.skillCalls)
          ? responseMetadata.skillCalls.length
          : 0,
      };
    });
    return {
      ...evaluation,
      results,
      wallTimeMs: firstSpan === null || lastSpan === null ? null : lastSpan - firstSpan,
    };
  } finally {
    database.close();
  }
}

export function summarizeRetrieval(result, trace) {
  const moments = Array.isArray(trace?.moments) ? trace.moments : [];
  const topMoment = moments.find((moment) => moment?.rank === 1) || moments[0];
  const bestByModality = new Map();
  for (const moment of moments) {
    for (const hit of Array.isArray(moment?.hits) ? moment.hits : []) {
      const iou = intervalIou(
        hit.start,
        hit.end,
        result.expectedStart,
        result.expectedEnd,
      );
      const current = bestByModality.get(hit.modality);
      if (current === undefined || (iou ?? -1) > (current.iou ?? -1)) {
        bestByModality.set(hit.modality, { ...hit, iou });
      }
    }
  }
  return {
    task: result.task,
    expectedStart: result.expectedStart,
    expectedEnd: result.expectedEnd,
    topMoment,
    topMomentIou: topMoment
      ? intervalIou(
        topMoment.start,
        topMoment.end,
        result.expectedStart,
        result.expectedEnd,
      )
      : null,
    bestByModality,
  };
}

function loadRetrievalTraces(results) {
  const jobIds = [...new Set(
    results
      .map((result) => result.sourceJobId)
      .filter((jobId) => typeof jobId === 'string' && jobId.length > 0),
  )];
  if (jobIds.length === 0) {
    return {};
  }
  const python = process.env.PROMPTFOO_PYTHON || 'python3';
  const script = fileURLToPath(new URL('./retrieval_trace.py', import.meta.url));
  const completed = spawnSync(python, [script, ...jobIds], {
    encoding: 'utf8',
    env: process.env,
  });
  if (completed.status !== 0) {
    throw new Error(completed.stderr.trim() || 'durable retrieval trace failed');
  }
  return parseJson(completed.stdout);
}

export function renderReport(
  evaluation,
  { showAll = false, showResponses = false, showRetrieval = false } = {},
) {
  const summaries = summarizeResults(evaluation.results);
  const created = Number.isFinite(evaluation.created_at)
    ? new Date(evaluation.created_at).toISOString()
    : String(evaluation.created_at);
  console.log(`\nEvaluation comparison: ${evaluation.id}`);
  console.log(`Created: ${created} | wall time: ${seconds(evaluation.wallTimeMs)}`);
  console.log('Quality and time:');
  console.table(summaries.map((summary) => ({
    condition: summary.condition,
    runs: summary.runs,
    passed: `${summary.passed}/${summary.runs}`,
    'mean IoU': fixed(summary.meanIou, 4),
    'R@.3': fixed(summary.recall03, 3),
    'R@.5': fixed(summary.recall05, 3),
    'R@.7': fixed(summary.recall07, 3),
    'start MAE': secondsValue(summary.meanStartError),
    'end MAE': secondsValue(summary.meanEndError),
    'duration MAE': secondsValue(summary.meanDurationError),
    'avg time': seconds(summary.meanLatencyMs),
    'total time': seconds(summary.totalLatencyMs),
  })));
  console.log('Token usage and estimated cost:');
  console.table(summaries.map((summary) => ({
    condition: summary.condition,
    total: integer(summary.totalTokens),
    input: integer(summary.promptTokens),
    'input cached': integer(summary.cachedTokens),
    'input uncached': integer(summary.uncachedPromptTokens),
    output: integer(summary.completionTokens),
    reasoning: integer(summary.reasoningTokens),
    requests: integer(summary.requests),
    'est. cost': money(summary.cost),
  })));
  console.log(
    '  Reasoning tokens are included in output tokens. Estimated cost is provider-reported; '
    + 'cached and uncached input can have different rates, so total tokens alone do not determine cost.',
  );
  console.log('Agent activity:');
  console.table(summaries.map((summary) => ({
    condition: summary.condition,
    items: summary.agentItems,
    'tool calls': summary.toolCalls,
    MCP: summary.mcpCalls,
    shell: summary.shellCalls,
    'ffmpeg/ffprobe': summary.mediaShellCalls,
    skill: summary.skillLoads,
  })));

  const on = summaries.find((summary) => summary.condition === 'vidxp-on');
  const off = summaries.find((summary) => summary.condition === 'vidxp-off');
  if (on && off) {
    const latencyDelta = on.meanLatencyMs - off.meanLatencyMs;
    const latencyPercent = off.meanLatencyMs
      ? Math.abs(latencyDelta) / off.meanLatencyMs * 100
      : null;
    const tokenDelta = Number.isFinite(on.totalTokens) && Number.isFinite(off.totalTokens)
      ? on.totalTokens - off.totalTokens
      : null;
    const tokenPercent = Number.isFinite(tokenDelta) && off.totalTokens
      ? Math.abs(tokenDelta) / off.totalTokens * 100
      : null;
    const uncachedDelta = Number.isFinite(on.uncachedPromptTokens)
      && Number.isFinite(off.uncachedPromptTokens)
      ? on.uncachedPromptTokens - off.uncachedPromptTokens
      : null;
    console.log('VidXP-on minus VidXP-off:');
    console.log(`  mean IoU: ${signed(on.meanIou - off.meanIou, 4)}`);
    console.log(
      `  average latency: ${signed(latencyDelta / 1000, 3)}s`
      + (Number.isFinite(latencyPercent)
        ? ` (${latencyPercent.toFixed(1)}% ${latencyDelta <= 0 ? 'faster' : 'slower'})`
        : ''),
    );
    console.log(
      `  total tokens: ${Number.isFinite(tokenDelta) && tokenDelta >= 0 ? '+' : ''}${integer(tokenDelta)}`
      + (Number.isFinite(tokenPercent)
        ? ` (${tokenPercent.toFixed(1)}% ${tokenDelta <= 0 ? 'fewer' : 'more'})`
        : ''),
    );
    console.log(
      `  uncached input tokens: ${Number.isFinite(uncachedDelta) && uncachedDelta >= 0 ? '+' : ''}`
      + integer(uncachedDelta),
    );
    const costDelta = Number.isFinite(on.cost) && Number.isFinite(off.cost)
      ? on.cost - off.cost
      : null;
    console.log(`  estimated cost: ${signedMoney(costDelta)}`);
  }

  if (evaluation.results.length <= 20 || showAll) {
    console.log('Per-run intervals:');
    const tasks = new Set(evaluation.results.map((result) => result.task));
    if (tasks.size === 1) {
      console.log(`  task: ${evaluation.results[0].task}`);
    }
    console.table(evaluation.results.map((result) => ({
      ...(tasks.size === 1 ? {} : { task: result.task }),
      condition: result.condition,
      pass: result.success ? 'yes' : 'NO',
      expected: interval(result.expectedStart, result.expectedEnd),
      predicted: interval(result.predictedStart, result.predictedEnd),
      'start Δ': signedSeconds(boundaryError(result.predictedStart, result.expectedStart)),
      'end Δ': signedSeconds(boundaryError(result.predictedEnd, result.expectedEnd)),
      'duration Δ': signedSeconds(durationError(result)),
      IoU: fixed(result.iou, 4),
      time: seconds(result.latencyMs),
    })));
    console.log('Per-run usage and tools:');
    console.table(evaluation.results.map((result) => ({
      ...(tasks.size === 1 ? {} : { task: result.task }),
      condition: result.condition,
      total: integer(result.totalTokens),
      input: integer(result.promptTokens),
      cached: integer(result.cachedTokens),
      uncached: integer(tokenDifference(result.promptTokens, result.cachedTokens)),
      output: integer(result.completionTokens),
      reasoning: integer(result.reasoningTokens),
      tools: result.toolCalls,
      MCP: result.mcpCalls,
      shell: result.shellCalls,
      media: result.mediaShellCalls,
      skill: result.skillLoads,
      'est. cost': money(result.cost),
    })));
  } else {
    console.log(`Per-run table omitted for ${evaluation.results.length} runs; use results --all to print it.`);
  }

  const failures = evaluation.results.filter((result) => !result.success);
  if (failures.length > 0) {
    console.log('Failures:');
    for (const failure of failures) {
      console.log(`  ${failure.task} [${failure.condition}]: ${failure.reason}`);
    }
  }

  if (showResponses) {
    console.log('Responses:');
    for (const result of evaluation.results) {
      console.log(`  ${result.task} [${result.condition}]`);
      console.log(`    answer: ${result.answer || 'n/a'}`);
      console.log(`    modalities: ${result.modalities.length ? result.modalities.join(', ') : 'n/a'}`);
      console.log(`    source job: ${result.sourceJobId || 'n/a'} | evidence items: ${result.evidenceCount}`);
    }
  }

  if (showRetrieval) {
    const traces = loadRetrievalTraces(evaluation.results);
    const retrievals = evaluation.results
      .filter((result) => traces[result.sourceJobId])
      .map((result) => summarizeRetrieval(result, traces[result.sourceJobId]));
    console.log('VidXP retrieval boundaries:');
    console.table(retrievals.map((retrieval) => ({
      task: retrieval.task,
      expected: interval(retrieval.expectedStart, retrieval.expectedEnd),
      'top fused': interval(retrieval.topMoment?.start, retrieval.topMoment?.end),
      'fused IoU': fixed(retrieval.topMomentIou, 4),
      modalities: Array.isArray(retrieval.topMoment?.modalities)
        ? retrieval.topMoment.modalities.join(', ')
        : 'n/a',
      hits: Array.isArray(retrieval.topMoment?.hits) ? retrieval.topMoment.hits.length : 0,
    })));
    console.log('Hits in the top fused interval:');
    console.table(retrievals.flatMap((retrieval) => (
      (Array.isArray(retrieval.topMoment?.hits) ? retrieval.topMoment.hits : []).map((hit) => ({
        task: retrieval.task,
        modality: hit.modality,
        rank: hit.rank,
        interval: interval(hit.start, hit.end),
        IoU: fixed(intervalIou(
          hit.start,
          hit.end,
          retrieval.expectedStart,
          retrieval.expectedEnd,
        ), 4),
      }))
    )));
    console.log('Best retrieved individual hit per modality:');
    console.table(retrievals.flatMap((retrieval) => (
      [...retrieval.bestByModality.entries()].map(([modality, hit]) => ({
        task: retrieval.task,
        modality,
        rank: hit.rank,
        interval: interval(hit.start, hit.end),
        IoU: fixed(hit.iou, 4),
      }))
    )));
  }
}

export function printLatestReport(options = {}) {
  renderReport(loadLatestEvaluation(), options);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  try {
    printLatestReport({
      showAll: process.argv.includes('--all'),
      showResponses: process.argv.includes('--responses'),
      showRetrieval: process.argv.includes('--retrieval'),
    });
  } catch (error) {
    console.error(`Could not report the latest evaluation: ${error.message}`);
    process.exitCode = 1;
  }
}
