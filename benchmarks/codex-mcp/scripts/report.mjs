import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';

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

function integer(value) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString('en-US') : 'n/a';
}

function money(value, digits = 6) {
  return Number.isFinite(value) ? `$${value.toFixed(digits)}` : 'n/a';
}

function interval(start, end) {
  return Number.isFinite(start) && Number.isFinite(end)
    ? `${start.toFixed(3)}–${end.toFixed(3)}s`
    : 'n/a';
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
      meanLatencyMs: mean(selected.map((result) => result.latencyMs)),
      totalLatencyMs: sum(selected.map((result) => result.latencyMs)),
      totalTokens: sum(selected.map((result) => result.totalTokens)),
      cachedTokens: sum(selected.map((result) => result.cachedTokens)),
      completionTokens: sum(selected.map((result) => result.completionTokens)),
      cost: sumOrNull(selected.map((result) => result.cost)),
      mcpCalls: sum(selected.map((result) => result.mcpCalls)),
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
      let mcpCalls = 0;
      let mediaShellCalls = 0;
      for (const span of spans) {
        const attributes = parseJson(span.attributes);
        if (span.name.startsWith('mcp vidxp/')) {
          mcpCalls += 1;
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
      traceStats.set(metadata.testIdx, { mcpCalls, mediaShellCalls });
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
        cachedTokens: response.tokenUsage?.cached,
        completionTokens: response.tokenUsage?.completion,
        cost: row.cost,
        mcpCalls: stats.mcpCalls || 0,
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

export function renderReport(evaluation, { showAll = false } = {}) {
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
    'avg time': seconds(summary.meanLatencyMs),
    'total time': seconds(summary.totalLatencyMs),
  })));
  console.log('Usage and tools:');
  console.table(summaries.map((summary) => ({
    condition: summary.condition,
    tokens: integer(summary.totalTokens),
    cached: integer(summary.cachedTokens),
    completion: integer(summary.completionTokens),
    'est. cost': money(summary.cost),
    MCP: summary.mcpCalls,
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
    const tokenDelta = on.totalTokens - off.totalTokens;
    const tokenPercent = off.totalTokens
      ? Math.abs(tokenDelta) / off.totalTokens * 100
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
      `  total tokens: ${tokenDelta >= 0 ? '+' : ''}${integer(tokenDelta)}`
      + (Number.isFinite(tokenPercent)
        ? ` (${tokenPercent.toFixed(1)}% ${tokenDelta <= 0 ? 'fewer' : 'more'})`
        : ''),
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
      IoU: fixed(result.iou, 4),
      time: seconds(result.latencyMs),
      tokens: integer(result.totalTokens),
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
}

export function printLatestReport(options = {}) {
  renderReport(loadLatestEvaluation(), options);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  try {
    printLatestReport({ showAll: process.argv.includes('--all') });
  } catch (error) {
    console.error(`Could not report the latest evaluation: ${error.message}`);
    process.exitCode = 1;
  }
}
