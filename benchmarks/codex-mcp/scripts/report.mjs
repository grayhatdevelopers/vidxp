import { homedir } from 'node:os';
import { join, resolve } from 'node:path';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { DatabaseSync } from 'node:sqlite';
import { spawnSync } from 'node:child_process';

const CONDITION_ORDER = ['vidxp-on', 'vidxp-off', 'clean-user', 'local-slm'];

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

function conditionCodexHome(condition) {
  const byCondition = {
    'vidxp-on': process.env.VIDXP_EVAL_VIDXP_ON_CODEX_HOME,
    'vidxp-off': process.env.VIDXP_EVAL_VIDXP_OFF_CODEX_HOME,
    'clean-user': process.env.VIDXP_EVAL_CLEAN_USER_CODEX_HOME,
  };
  return byCondition[condition] || process.env.VIDXP_EVAL_CODEX_HOME;
}

function findRollout(codexHome, sessionId) {
  const sessions = codexHome && join(codexHome, 'sessions');
  if (!sessions || !existsSync(sessions) || !sessionId) {
    return null;
  }
  const pending = [sessions];
  while (pending.length > 0) {
    const directory = pending.pop();
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const path = join(directory, entry.name);
      if (entry.isDirectory()) {
        pending.push(path);
      } else if (entry.isFile() && entry.name.endsWith(`${sessionId}.jsonl`)) {
        return path;
      }
    }
  }
  return null;
}

function rolloutModelTurns(condition, sessionId) {
  const conditionHome = conditionCodexHome(condition);
  const path = findRollout(conditionHome, sessionId)
    || (conditionHome === process.env.VIDXP_EVAL_CODEX_HOME
      ? null
      : findRollout(process.env.VIDXP_EVAL_CODEX_HOME, sessionId));
  if (!path) {
    return null;
  }
  let modelTurns = 0;
  let lastTotal = -1;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    if (!line) continue;
    const event = parseJson(line, null);
    const payload = event?.payload;
    if (event?.type === 'event_msg' && payload?.type === 'token_count') {
      const info = payload.info;
      const cumulative = Number(info?.total_token_usage?.total_tokens);
      if (info?.last_token_usage && cumulative > lastTotal) {
        modelTurns += 1;
        lastTotal = cumulative;
      }
    }
  }
  return modelTurns;
}

export function summarizeRecordedItems(raw) {
  const turn = typeof raw === 'string' ? parseJson(raw, null) : raw;
  const items = Array.isArray(turn?.items) ? turn.items : null;
  if (!items) {
    return null;
  }
  let toolCalls = 0;
  let mcpCalls = 0;
  let shellCalls = 0;
  for (const item of items) {
    if (item?.type === 'command_execution') {
      shellCalls += 1;
      toolCalls += 1;
    } else if (typeof item?.type === 'string' && item.type.endsWith('_tool_call')) {
      toolCalls += 1;
    }
    if (item?.type === 'mcp_tool_call' && item.server === 'vidxp') {
      mcpCalls += 1;
    }
  }
  return {
    agentItems: items.length,
    toolCalls,
    mcpCalls,
    shellCalls,
  };
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

function eventCoverage(start, end, expectedStart, expectedEnd, targetChunkSeconds) {
  if (![start, end, expectedStart, expectedEnd, targetChunkSeconds].every(Number.isFinite)) {
    return null;
  }
  const intersection = Math.max(0, Math.min(end, expectedEnd) - Math.max(start, expectedStart));
  const usefulDuration = Math.min(expectedEnd - expectedStart, targetChunkSeconds);
  return usefulDuration > 0 ? Math.min(1, intersection / usefulDuration) : null;
}

export function assertionReason(grading, metric) {
  const component = (Array.isArray(grading?.componentResults) ? grading.componentResults : [])
    .find((result) => result?.assertion?.metric === metric);
  return typeof component?.reason === 'string' ? component.reason : '';
}

function outputCandidates(output) {
  if (Array.isArray(output?.candidates)) {
    return output.candidates;
  }
  if (output && ('start_seconds' in output || 'end_seconds' in output)) {
    return [output];
  }
  return [];
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
  const conditions = [
    ...CONDITION_ORDER,
    ...new Set(results.map((result) => result.condition).filter(
      (condition) => !CONDITION_ORDER.includes(condition),
    )),
  ];
  return conditions.map((condition) => {
    const selected = results.filter((result) => result.condition === condition);
    const valid = selected.filter((result) => result.integrityPassed === true);
    const scored = valid.filter((result) => Number.isFinite(result.chunkHit));
    return {
      condition,
      runs: selected.length,
      passed: selected.filter((result) => result.success).length,
      integrityPassed: valid.length,
      chunkHits: scored.filter((result) => result.chunkHit === 1).length,
      top1ChunkHits: scored.filter((result) => (
        (Number.isFinite(result.top1ChunkHit) ? result.top1ChunkHit : result.chunkHit) === 1
      )).length,
      chunkScored: scored.length,
      rawChunkHits: selected.filter((result) => result.chunkHit === 1).length,
      rawChunkScored: selected.filter((result) => Number.isFinite(result.chunkHit)).length,
      chunkHitRate: mean(scored.map((result) => result.chunkHit)),
      top1ChunkHitRate: mean(scored.map((result) => (
        Number.isFinite(result.top1ChunkHit) ? result.top1ChunkHit : result.chunkHit
      ))),
      meanChunkMrr: mean(scored.map((result) => (
        Number.isFinite(result.chunkMrr) ? result.chunkMrr : result.chunkHit
      ))),
      meanCandidateCount: mean(scored.map((result) => (
        Number.isFinite(result.candidateCount) ? result.candidateCount : 1
      ))),
      meanEventCoverage: mean(scored.map((result) => result.eventCoverage)),
      durationInRangeRate: mean(scored.map((result) => result.durationInRange)),
      meanIou: mean(scored.map((result) => result.iou)),
      meanBestIou: mean(scored.map((result) => (
        Number.isFinite(result.bestIou) ? result.bestIou : result.iou
      ))),
      recall03: mean(scored.map((result) => result.recall03)),
      recall05: mean(scored.map((result) => result.recall05)),
      recall07: mean(scored.map((result) => result.recall07)),
      recallAt3_03: mean(scored.map((result) => (
        Number.isFinite(result.recallAt3_03) ? result.recallAt3_03 : result.recall03
      ))),
      recallAt3_05: mean(scored.map((result) => (
        Number.isFinite(result.recallAt3_05) ? result.recallAt3_05 : result.recall05
      ))),
      recallAt3_07: mean(scored.map((result) => (
        Number.isFinite(result.recallAt3_07) ? result.recallAt3_07 : result.recall07
      ))),
      meanStartError: mean(scored.map((result) => (
        absolute(boundaryError(result.predictedStart, result.expectedStart))
      ))),
      meanEndError: mean(scored.map((result) => (
        absolute(boundaryError(result.predictedEnd, result.expectedEnd))
      ))),
      meanDurationError: mean(scored.map((result) => absolute(durationError(result)))),
      meanLatencyMs: mean(selected.map((result) => result.latencyMs)),
      totalLatencyMs: sum(selected.map((result) => result.latencyMs)),
      meanTotalTokens: mean(selected.map((result) => result.totalTokens)),
      totalTokens: sumOrNull(selected.map((result) => result.totalTokens)),
      meanPromptTokens: mean(selected.map((result) => result.promptTokens)),
      promptTokens: sumOrNull(selected.map((result) => result.promptTokens)),
      meanUncachedPromptTokens: mean(selected.map((result) => (
        tokenDifference(result.promptTokens, result.cachedTokens)
      ))),
      uncachedPromptTokens: sumOrNull(selected.map((result) => (
        tokenDifference(result.promptTokens, result.cachedTokens)
      ))),
      meanCachedTokens: mean(selected.map((result) => result.cachedTokens)),
      cachedTokens: sumOrNull(selected.map((result) => result.cachedTokens)),
      meanCompletionTokens: mean(selected.map((result) => result.completionTokens)),
      completionTokens: sumOrNull(selected.map((result) => result.completionTokens)),
      meanReasoningTokens: mean(selected.map((result) => result.reasoningTokens)),
      reasoningTokens: sumOrNull(selected.map((result) => result.reasoningTokens)),
      requests: sumOrNull(selected.map((result) => result.requests)),
      meanCost: mean(selected.map((result) => result.cost)),
      cost: sumOrNull(selected.map((result) => result.cost)),
      modelTurns: sum(selected.map((result) => result.modelTurns)),
      agentItems: sum(selected.map((result) => result.agentItems)),
      toolCalls: sum(selected.map((result) => result.toolCalls)),
      mcpCalls: sum(selected.map((result) => result.mcpCalls)),
      shellCalls: sum(selected.map((result) => result.shellCalls)),
      skillLoads: sum(selected.map((result) => result.skillLoads)),
    };
  }).filter((summary) => summary.runs > 0);
}

export function summarizePrimaryPairs(results) {
  const byCondition = new Map(CONDITION_ORDER.slice(0, 2).map((condition) => [condition, new Map()]));
  for (const result of results) {
    const selected = byCondition.get(result.condition);
    if (selected) {
      selected.set(`${result.task}\u0000${result.repetition}`, result);
    }
  }
  const on = byCondition.get('vidxp-on');
  const off = byCondition.get('vidxp-off');
  const keys = new Set([...on.keys(), ...off.keys()]);
  const pairs = [...keys].map((key) => ({ on: on.get(key), off: off.get(key) }));
  const valid = pairs.filter(({ on: onResult, off: offResult }) => (
    onResult?.integrityPassed === true
    && offResult?.integrityPassed === true
    && Number.isFinite(onResult?.chunkHit)
    && Number.isFinite(offResult?.chunkHit)
    && Number.isFinite(onResult?.totalTokens)
    && Number.isFinite(offResult?.totalTokens)
  ));
  return {
    totalPairs: pairs.length,
    validPairs: valid.length,
    pairs: valid,
    results: valid.flatMap(({ on: onResult, off: offResult }) => [onResult, offResult]),
  };
}

function deterministicRescore(results, evaluationId) {
  const python = process.env.PROMPTFOO_PYTHON || 'python3';
  const script = fileURLToPath(new URL('./rescore_eval.py', import.meta.url));
  const manifest = parseJson(readFileSync(
    fileURLToPath(new URL('../tasks/longvale-part9-pilot.json', import.meta.url)),
    'utf8',
  ), []);
  const durationByTask = new Map(manifest.map((task) => [task.id, task.duration_seconds]));
  const input = results.map((result) => ({
    test_idx: result.testIdx,
    output: result.outputText,
    vars: {
      ...result.testVars,
      duration_seconds: durationByTask.get(result.task) ?? result.testVars.duration_seconds,
    },
    metadata: { evaluationId, ...result.providerMetadata },
    spans: result.traceSpans,
  }));
  const completed = spawnSync(python, [script], {
    encoding: 'utf8',
    env: process.env,
    input: JSON.stringify(input),
    maxBuffer: 16 * 1024 * 1024,
  });
  if (completed.status !== 0) {
    throw new Error(completed.stderr.trim() || 'deterministic rescore failed');
  }
  const byIndex = new Map(parseJson(completed.stdout, []).map((item) => [item.test_idx, item]));
  for (const result of results) {
    const audit = byIndex.get(result.testIdx);
    if (!audit) {
      throw new Error(`deterministic rescore omitted test ${result.testIdx}`);
    }
    const named = audit.temporal?.namedScores || {};
    result.integrityPassed = audit.boundary?.pass === true;
    result.integrityReason = audit.boundary?.reason || '';
    result.qualityReason = audit.temporal?.reason || '';
    result.chunkHit = Number.isFinite(named.bounded_chunk_hit)
      ? named.bounded_chunk_hit : null;
    result.top1ChunkHit = Number.isFinite(named.bounded_chunk_hit_at_1)
      ? named.bounded_chunk_hit_at_1 : result.chunkHit;
    result.chunkMrr = Number.isFinite(named.bounded_chunk_mrr)
      ? named.bounded_chunk_mrr : result.chunkHit;
    result.candidateCount = Number.isFinite(named.candidate_count)
      ? named.candidate_count : result.candidateCount;
    result.eventCoverage = Number.isFinite(named.event_coverage)
      ? named.event_coverage : null;
    result.durationInRange = Number.isFinite(named.chunk_duration_in_range)
      ? named.chunk_duration_in_range : null;
    result.iou = Number.isFinite(named.temporal_iou) ? named.temporal_iou : null;
    result.bestIou = Number.isFinite(named.best_temporal_iou)
      ? named.best_temporal_iou : result.iou;
    result.recall03 = Number.isFinite(named.r1_tiou_0_3) ? named.r1_tiou_0_3 : null;
    result.recall05 = Number.isFinite(named.r1_tiou_0_5) ? named.r1_tiou_0_5 : null;
    result.recall07 = Number.isFinite(named.r1_tiou_0_7) ? named.r1_tiou_0_7 : null;
    result.recallAt3_03 = Number.isFinite(named.r3_tiou_0_3)
      ? named.r3_tiou_0_3 : result.recall03;
    result.recallAt3_05 = Number.isFinite(named.r3_tiou_0_5)
      ? named.r3_tiou_0_5 : result.recall05;
    result.recallAt3_07 = Number.isFinite(named.r3_tiou_0_7)
      ? named.r3_tiou_0_7 : result.recall07;
  }
}

export function loadLatestEvaluation({ rescore = false } = {}) {
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
        spans: spans.map((span) => ({
          name: span.name,
          start_time: span.start_time,
          end_time: span.end_time,
          attributes: parseJson(span.attributes),
        })),
      });
    }

    const results = rows.map((row) => {
      const testCase = parseJson(row.test_case);
      const response = parseJson(row.response);
      const output = parseJson(response.output);
      const candidates = outputCandidates(output);
      const topCandidate = candidates[0] || {};
      const namedScores = parseJson(row.named_scores);
      const grading = parseJson(row.grading_result);
      const responseMetadata = response.metadata || {};
      const stats = traceStats.get(row.test_idx) || {};
      const recordedItems = summarizeRecordedItems(response.raw) || stats;
      const rolloutTurns = rolloutModelTurns(
        testCase.vars?.condition || 'unknown',
        response.sessionId,
      );
      const modelTurns = Number.isFinite(rolloutTurns)
        ? rolloutTurns
        : (Number.isFinite(responseMetadata.modelTurns) ? responseMetadata.modelTurns : 0);
      return {
        task: testCase.metadata?.task_id || testCase.vars?.id || String(row.test_idx),
        machineId: testCase.metadata?.machine_id || process.env.VIDXP_EVAL_MACHINE_ID,
        condition: testCase.vars?.condition || 'unknown',
        expectedVidxp: testCase.vars?.expected_vidxp === true,
        evaluationMode: testCase.vars?.evaluation_mode
          || testCase.metadata?.evaluation_mode
          || 'unknown',
        repetition: testCase.vars?.repetition || testCase.metadata?.repetition || 1,
        testIdx: row.test_idx,
        testVars: testCase.vars || {},
        providerMetadata: responseMetadata,
        outputText: typeof response.output === 'string' ? response.output : '',
        traceSpans: stats.spans || [],
        success: row.success === 1,
        reason: grading.reason || row.error || '',
        integrityPassed: namedScores.ablation_boundary === 1,
        integrityReason: namedScores.ablation_boundary === 1
          ? ''
          : (assertionReason(grading, 'ablation_boundary') || row.error || grading.reason || ''),
        qualityReason: assertionReason(grading, 'temporal_grounding')
          || row.error || grading.reason || '',
        expectedStart: testCase.vars?.expected_start,
        expectedEnd: testCase.vars?.expected_end,
        predictedStart: topCandidate.start_seconds,
        predictedEnd: topCandidate.end_seconds,
        answer: output.answer,
        modalities: Array.isArray(topCandidate.modalities) ? topCandidate.modalities : [],
        sourceJobId: output.source_job_id,
        evidenceCount: candidates.reduce(
          (count, candidate) => count + (
            Array.isArray(candidate?.evidence_ids)
              ? candidate.evidence_ids.length
              : (Array.isArray(candidate?.evidence) ? candidate.evidence.length : 0)
          ),
          0,
        ),
        candidateCount: candidates.length,
        rankedCandidates: Array.isArray(output.candidates),
        chunkHit: Number.isFinite(namedScores.bounded_chunk_hit)
          ? namedScores.bounded_chunk_hit
          : null,
        top1ChunkHit: Number.isFinite(namedScores.bounded_chunk_hit_at_1)
          ? namedScores.bounded_chunk_hit_at_1
          : (Number.isFinite(namedScores.bounded_chunk_hit)
            ? namedScores.bounded_chunk_hit : null),
        chunkMrr: Number.isFinite(namedScores.bounded_chunk_mrr)
          ? namedScores.bounded_chunk_mrr
          : (Number.isFinite(namedScores.bounded_chunk_hit)
            ? namedScores.bounded_chunk_hit : null),
        eventCoverage: Number.isFinite(namedScores.event_coverage)
          ? namedScores.event_coverage
          : null,
        durationInRange: Number.isFinite(namedScores.chunk_duration_in_range)
          ? namedScores.chunk_duration_in_range
          : null,
        iou: Number.isFinite(namedScores.temporal_iou) ? namedScores.temporal_iou : null,
        bestIou: Number.isFinite(namedScores.best_temporal_iou)
          ? namedScores.best_temporal_iou
          : (Number.isFinite(namedScores.temporal_iou) ? namedScores.temporal_iou : null),
        recall03: Number.isFinite(namedScores.r1_tiou_0_3)
          ? namedScores.r1_tiou_0_3
          : null,
        recall05: Number.isFinite(namedScores.r1_tiou_0_5)
          ? namedScores.r1_tiou_0_5
          : null,
        recall07: Number.isFinite(namedScores.r1_tiou_0_7)
          ? namedScores.r1_tiou_0_7
          : null,
        recallAt3_03: Number.isFinite(namedScores.r3_tiou_0_3)
          ? namedScores.r3_tiou_0_3
          : (Number.isFinite(namedScores.r1_tiou_0_3)
            ? namedScores.r1_tiou_0_3 : null),
        recallAt3_05: Number.isFinite(namedScores.r3_tiou_0_5)
          ? namedScores.r3_tiou_0_5
          : (Number.isFinite(namedScores.r1_tiou_0_5)
            ? namedScores.r1_tiou_0_5 : null),
        recallAt3_07: Number.isFinite(namedScores.r3_tiou_0_7)
          ? namedScores.r3_tiou_0_7
          : (Number.isFinite(namedScores.r1_tiou_0_7)
            ? namedScores.r1_tiou_0_7 : null),
        latencyMs: row.latency_ms,
        totalTokens: response.tokenUsage?.total,
        promptTokens: response.tokenUsage?.prompt,
        cachedTokens: response.tokenUsage?.cached,
        completionTokens: response.tokenUsage?.completion,
        reasoningTokens: response.tokenUsage?.completionDetails?.reasoning,
        requests: response.tokenUsage?.numRequests,
        cost: row.cost,
        modelTurns,
        agentItems: recordedItems.agentItems || 0,
        toolCalls: recordedItems.toolCalls || 0,
        mcpCalls: recordedItems.mcpCalls || 0,
        shellCalls: recordedItems.shellCalls || 0,
        skillLoads: Array.isArray(responseMetadata.skillCalls)
          ? responseMetadata.skillCalls.length
          : 0,
      };
    });
    if (rescore) {
      deterministicRescore(results, evaluation.id);
    }
    return {
      ...evaluation,
      results,
      mode: (() => {
        const modes = new Set(results.map((result) => result.evaluationMode));
        return modes.size === 1 ? [...modes][0] : 'unknown';
      })(),
      wallTimeMs: firstSpan === null || lastSpan === null ? null : lastSpan - firstSpan,
      machineId: (() => {
        const ids = new Set(results.map((result) => result.machineId).filter(Boolean));
        return ids.size === 1 ? [...ids][0] : 'unknown';
      })(),
      rescored: rescore,
    };
  } finally {
    database.close();
  }
}

export function summarizeRetrieval(result, trace) {
  const moments = (Array.isArray(trace?.moments) ? trace.moments : [])
    .slice()
    .sort((left, right) => (left?.rank ?? Infinity) - (right?.rank ?? Infinity));
  const topMoment = moments.find((moment) => moment?.rank === 1) || moments[0];
  const targetChunkSeconds = Number(result.testVars?.target_chunk_seconds) > 0
    ? Number(result.testVars.target_chunk_seconds)
    : 10;
  const minEventCoverage = Number(result.testVars?.min_event_coverage) > 0
    ? Number(result.testVars.min_event_coverage)
    : 0.5;
  const surfaceCandidates = (Array.isArray(trace?.surface_candidates)
    ? trace.surface_candidates : [])
    .filter((candidate) => candidate?.state === 'ready')
    .slice()
    .sort((left, right) => (left?.rank ?? Infinity) - (right?.rank ?? Infinity));
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
        bestByModality.set(hit.modality, {
          ...hit,
          iou,
          fusedRank: moment.rank,
          fusedStart: moment.start,
          fusedEnd: moment.end,
        });
      }
    }
  }
  return {
    task: result.task,
    repetition: result.repetition,
    condition: result.condition,
    finalChunkHit: result.chunkHit,
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
    momentIous: moments.map((moment) => intervalIou(
      moment.start,
      moment.end,
      result.expectedStart,
      result.expectedEnd,
    )),
    surfaceCandidates,
    surfaceRanks: surfaceCandidates.map((candidate, index) => (
      Number.isFinite(candidate.rank) ? candidate.rank : index + 1
    )),
    surfaceCoverages: surfaceCandidates.map((candidate) => eventCoverage(
      candidate.start,
      candidate.end,
      result.expectedStart,
      result.expectedEnd,
      targetChunkSeconds,
    )),
    minEventCoverage,
    bestByModality,
  };
}

export function summarizeSurfaceRecall(retrievals, depth) {
  const scored = retrievals.filter((retrieval) => retrieval.surfaceCoverages.some(Number.isFinite));
  const bestCoverages = scored.map((retrieval) => {
    const candidates = retrieval.surfaceCoverages.filter((coverage, index) => (
      Number.isFinite(coverage) && retrieval.surfaceRanks[index] <= depth
    ));
    return candidates.length > 0 ? Math.max(...candidates) : 0;
  });
  const hits = bestCoverages.filter((coverage, index) => (
    coverage >= scored[index].minEventCoverage
  )).length;
  return {
    hits,
    scored: scored.length,
    rate: scored.length > 0 ? hits / scored.length : null,
    meanBestCoverage: mean(bestCoverages),
  };
}

export function summarizeSurfaceTransfer(retrievals, depth) {
  const summary = {
    surfacedAndReturned: 0,
    surfacedOnly: 0,
    returnedOnly: 0,
    neither: 0,
  };
  for (const retrieval of retrievals) {
    const candidates = retrieval.surfaceCoverages.filter((coverage, index) => (
      Number.isFinite(coverage) && retrieval.surfaceRanks[index] <= depth
    ));
    if (candidates.length === 0 || !Number.isFinite(retrieval.finalChunkHit)) {
      continue;
    }
    const surfaced = Math.max(...candidates) >= retrieval.minEventCoverage;
    const returned = retrieval.finalChunkHit === 1;
    if (surfaced && returned) summary.surfacedAndReturned += 1;
    else if (surfaced) summary.surfacedOnly += 1;
    else if (returned) summary.returnedOnly += 1;
    else summary.neither += 1;
  }
  return summary;
}

function retrievalRecallAt(retrievals, depth, threshold) {
  return mean(retrievals.map((retrieval) => {
    const candidates = retrieval.momentIous.slice(0, depth).filter(Number.isFinite);
    return candidates.length > 0 && Math.max(...candidates) >= threshold ? 1 : 0;
  }));
}

export function loadRetrievalTraces(results) {
  const jobIds = [...new Set(
    results
      .filter((result) => result.expectedVidxp && result.integrityPassed)
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
  { showAll = false, showResponses = false, showRetrieval = true } = {},
) {
  const summaries = summarizeResults(evaluation.results);
  const created = Number.isFinite(evaluation.created_at)
    ? new Date(evaluation.created_at).toISOString()
    : String(evaluation.created_at);
  const taskCount = new Set(evaluation.results.map((result) => result.task)).size;
  const isSmoke = evaluation.mode === 'smoke'
    || (evaluation.mode === 'unknown' && taskCount === 1);
  const runType = isSmoke ? 'development smoke' : evaluation.mode;
  const rankedCandidates = evaluation.results.some((result) => result.rankedCandidates);
  const primaryPairs = summarizePrimaryPairs(evaluation.results);
  const pairedSummaries = summarizeResults(primaryPairs.results);
  console.log(`\nEvaluation comparison: ${evaluation.id}`);
  console.log(
    `Run type: ${runType} | machine: ${evaluation.machineId || 'unknown'} `
    + `| created: ${created} | wall time: ${seconds(evaluation.wallTimeMs)}`,
  );
  const localResults = evaluation.results.filter((result) => result.condition === 'local-slm');
  if (localResults.length > 0) {
    const models = new Set(localResults.map((result) => {
      const model = result.providerMetadata?.model;
      return model?.provider && model?.model ? `${model.provider}/${model.model}` : null;
    }).filter(Boolean));
    const coldStarts = localResults.filter(
      (result) => result.providerMetadata?.coldStart === true,
    ).length;
    console.log(
      `Local agent: ${models.size === 1 ? [...models][0] : 'unknown'} | `
      + `managed runtime cold starts: ${coldStarts}/${localResults.length}`,
    );
  }
  const passedAssertions = evaluation.results.filter((result) => result.success).length;
  console.log(
    `${evaluation.rescored ? 'Original at-run Promptfoo assertions' : 'Stored Promptfoo assertions'}: `
    + `${passedAssertions === evaluation.results.length ? 'PASS' : 'FAIL'}`
    + ` (${passedAssertions}/${evaluation.results.length} runs passed every at-run assertion)`,
  );
  if (evaluation.rescored) {
    console.log(
      'Current deterministic audit: saved responses and traces rescored against the current '
      + 'scorer and validated media durations; no agent or model calls made.',
    );
  }
  console.log('Product outcome:');
  console.table(summaries.map((summary) => ({
    condition: summary.condition,
    runs: summary.runs,
    integrity: `${summary.integrityPassed}/${summary.runs}`,
    scorable: `${summary.chunkScored}/${summary.runs}`,
    [rankedCandidates ? 'valid hit@3' : 'valid hits']: summary.chunkScored
      ? `${summary.chunkHits}/${summary.chunkScored}`
      : 'n/a',
    ...(rankedCandidates ? {
      'valid hit@1': summary.chunkScored
        ? `${summary.top1ChunkHits}/${summary.chunkScored}`
        : 'n/a',
      MRR: fixed(summary.meanChunkMrr, 3),
      candidates: fixed(summary.meanCandidateCount, 2),
    } : {}),
    'all output hits': summary.rawChunkScored
      ? `${summary.rawChunkHits}/${summary.rawChunkScored}`
      : 'n/a',
    [rankedCandidates ? 'hit@3 rate' : 'hit rate']: fixed(summary.chunkHitRate, 3),
    coverage: fixed(summary.meanEventCoverage, 3),
    'duration valid': fixed(summary.durationInRangeRate, 3),
    'avg time': seconds(summary.meanLatencyMs),
    'total time': seconds(summary.totalLatencyMs),
  })));
  console.log(
    `  Primary quality: ${rankedCandidates ? 'at least one of up to three ordered ' : 'one '}`
    + '8–12s clip covers at least half of the event available to a 10s clip. '
    + 'Quality rates exclude runs that violated their condition. Time, tokens, and activity include '
    + 'all runs. Boundary IoU and R@ thresholds remain secondary diagnostics.',
  );
  console.log('Boundary diagnostics (secondary):');
  console.table(summaries.map((summary) => ({
    condition: summary.condition,
    'top-1 IoU': fixed(summary.meanIou, 4),
    ...(rankedCandidates ? { 'best@3 IoU': fixed(summary.meanBestIou, 4) } : {}),
    'R1@.3': fixed(summary.recall03, 3),
    'R1@.5': fixed(summary.recall05, 3),
    'R1@.7': fixed(summary.recall07, 3),
    ...(rankedCandidates ? {
      'R3@.3': fixed(summary.recallAt3_03, 3),
      'R3@.5': fixed(summary.recallAt3_05, 3),
      'R3@.7': fixed(summary.recallAt3_07, 3),
    } : {}),
    'start MAE': secondsValue(summary.meanStartError),
    'end MAE': secondsValue(summary.meanEndError),
    'duration MAE': secondsValue(summary.meanDurationError),
  })));
  console.log('Token usage and Promptfoo cost:');
  console.table(summaries.map((summary) => ({
    condition: summary.condition,
    runs: summary.runs,
    'avg total': integer(summary.meanTotalTokens),
    'avg input': integer(summary.meanPromptTokens),
    'avg cached': integer(summary.meanCachedTokens),
    'avg uncached': integer(summary.meanUncachedPromptTokens),
    'avg output': integer(summary.meanCompletionTokens),
    'avg reasoning': integer(summary.meanReasoningTokens),
    'all tokens': integer(summary.totalTokens),
    'avg cost': money(summary.meanCost),
    'all cost': money(summary.cost),
  })));
  console.log(
    '  Reasoning tokens are included in output tokens. Cost is Promptfoo\'s supplied provider '
    + 'estimate, kept unchanged as a consistent comparison metric; it is not an end-user bill or '
    + 'a verified Codex-plan charge. The local provider reports zero external provider charge; '
    + 'local compute is not priced.',
  );
  console.log('Agent activity:');
  console.table(summaries.map((summary) => ({
    condition: summary.condition,
    'model requests': summary.requests,
    turns: summary.modelTurns,
    items: summary.agentItems,
    'tool calls': summary.toolCalls,
    MCP: summary.mcpCalls,
    shell: summary.shellCalls,
    skill: summary.skillLoads,
  })));
  console.log(
    '  Items and tool-type counts come from Promptfoo\'s saved provider response. Model turns '
    + 'come from Codex rollout events or the local provider\'s reported request count.',
  );

  const on = summaries.find((summary) => summary.condition === 'vidxp-on');
  const off = summaries.find((summary) => summary.condition === 'vidxp-off');
  const cleanUser = summaries.find((summary) => summary.condition === 'clean-user');
  const pairedOn = pairedSummaries.find((summary) => summary.condition === 'vidxp-on');
  const pairedOff = pairedSummaries.find((summary) => summary.condition === 'vidxp-off');
  if (on && off && pairedOn && pairedOff) {
    const latencyDelta = pairedOn.meanLatencyMs - pairedOff.meanLatencyMs;
    const latencyPercent = pairedOff.meanLatencyMs
      ? Math.abs(latencyDelta) / pairedOff.meanLatencyMs * 100
      : null;
    const tokenDelta = Number.isFinite(pairedOn.meanTotalTokens)
      && Number.isFinite(pairedOff.meanTotalTokens)
      ? pairedOn.meanTotalTokens - pairedOff.meanTotalTokens
      : null;
    const tokenPercent = Number.isFinite(tokenDelta) && pairedOff.meanTotalTokens
      ? Math.abs(tokenDelta) / pairedOff.meanTotalTokens * 100
      : null;
    const uncachedDelta = Number.isFinite(pairedOn.meanUncachedPromptTokens)
      && Number.isFinite(pairedOff.meanUncachedPromptTokens)
      ? pairedOn.meanUncachedPromptTokens - pairedOff.meanUncachedPromptTokens
      : null;
    console.log(
      `Matched condition-valid, scorable VidXP-on minus VidXP-off (${primaryPairs.validPairs}`
      + `/${primaryPairs.totalPairs} pairs):`,
    );
    const chunkHitDelta = Number.isFinite(pairedOn.chunkHitRate)
      && Number.isFinite(pairedOff.chunkHitRate)
      ? pairedOn.chunkHitRate - pairedOff.chunkHitRate
      : null;
    console.log(
      `  bounded chunk hit${rankedCandidates ? '@3' : ''} rate: `
      + signed(chunkHitDelta, 3),
    );
    if (rankedCandidates) {
      console.log(
        `  bounded chunk hit@1 rate: `
        + signed(pairedOn.top1ChunkHitRate - pairedOff.top1ChunkHitRate, 3),
      );
      console.log(
        `  bounded chunk MRR: `
        + signed(pairedOn.meanChunkMrr - pairedOff.meanChunkMrr, 3),
      );
    }
    console.log(`  top-1 mean IoU: ${signed(pairedOn.meanIou - pairedOff.meanIou, 4)}`);
    if (rankedCandidates) {
      console.log(
        `  best@3 mean IoU: ${signed(pairedOn.meanBestIou - pairedOff.meanBestIou, 4)}`,
      );
    }
    console.log(
      `  average latency: ${signed(latencyDelta / 1000, 3)}s`
      + (Number.isFinite(latencyPercent)
        ? ` (${latencyPercent.toFixed(1)}% ${latencyDelta <= 0 ? 'faster' : 'slower'})`
        : ''),
    );
    console.log(
      `  average tokens: ${Number.isFinite(tokenDelta) && tokenDelta >= 0 ? '+' : ''}${integer(tokenDelta)}`
      + (Number.isFinite(tokenPercent)
        ? ` (${tokenPercent.toFixed(1)}% ${tokenDelta <= 0 ? 'fewer' : 'more'})`
        : ''),
    );
    console.log(
      `  average uncached input tokens: ${Number.isFinite(uncachedDelta) && uncachedDelta >= 0 ? '+' : ''}`
      + integer(uncachedDelta),
    );
    const costDelta = Number.isFinite(pairedOn.meanCost) && Number.isFinite(pairedOff.meanCost)
      ? pairedOn.meanCost - pairedOff.meanCost
      : null;
    console.log(`  average Promptfoo cost: ${signedMoney(costDelta)}`);
    const latencyWins = primaryPairs.pairs.filter(({ on: onResult, off: offResult }) => (
      Number.isFinite(onResult.latencyMs)
      && Number.isFinite(offResult.latencyMs)
      && onResult.latencyMs < offResult.latencyMs
    )).length;
    const tokenWins = primaryPairs.pairs.filter(({ on: onResult, off: offResult }) => (
      onResult.totalTokens < offResult.totalTokens
    )).length;
    const costWins = primaryPairs.pairs.filter(({ on: onResult, off: offResult }) => (
      Number.isFinite(onResult.cost)
      && Number.isFinite(offResult.cost)
      && onResult.cost < offResult.cost
    )).length;
    console.log(
      `  pairwise efficiency wins: faster ${latencyWins}/${primaryPairs.validPairs}; `
      + `fewer tokens ${tokenWins}/${primaryPairs.validPairs}; lower Promptfoo cost `
      + `${costWins}/${primaryPairs.validPairs}`,
    );
    if (evaluation.mode === 'pilot') {
      const integrityComplete = primaryPairs.validPairs === primaryPairs.totalPairs
        && primaryPairs.totalPairs === on.runs
        && primaryPairs.totalPairs === off.runs;
      const productGateAvailable = integrityComplete
        && Number.isFinite(chunkHitDelta)
        && Number.isFinite(tokenDelta);
      const productGatePassed = productGateAvailable && chunkHitDelta >= 0 && tokenDelta < 0;
      console.log(
        `  product gate: ${productGateAvailable
          ? (productGatePassed ? 'PASS' : 'FAIL')
          : 'NOT SCORED (incomplete valid/scorable pairs)'}`
        + ` (VidXP must match or improve bounded-chunk hit${rankedCandidates ? '@3' : ''} `
        + 'rate and use fewer total tokens)',
      );
    } else {
      console.log('  product gate: NOT SCORED (development smoke)');
    }
  }

  if (cleanUser) {
    console.log('Clean-user supporting comparisons:');
    console.table([off, on].filter(Boolean).map((reference) => ({
      comparison: `clean-user minus ${reference.condition}`,
      [rankedCandidates ? 'hit@3 Δ' : 'hit-rate Δ']:
        signed(cleanUser.chunkHitRate - reference.chunkHitRate, 3),
      'top-1 IoU Δ': signed(cleanUser.meanIou - reference.meanIou, 4),
      'avg time Δ': signedSeconds((cleanUser.meanLatencyMs - reference.meanLatencyMs) / 1000),
      'avg tokens Δ': integer(cleanUser.meanTotalTokens - reference.meanTotalTokens),
      'avg cost Δ': signedMoney(cleanUser.meanCost - reference.meanCost),
    })));
  }

  if (evaluation.results.length <= 20 || showAll) {
    console.log('Per-run product result:');
    const tasks = new Set(evaluation.results.map((result) => result.task));
    if (tasks.size === 1) {
      console.log(`  task: ${evaluation.results[0].task}`);
    }
    const repeated = evaluation.results.some((result) => result.repetition > 1);
    console.table(evaluation.results.map((result) => ({
      ...(tasks.size === 1 ? {} : { task: result.task }),
      ...(repeated ? { repetition: result.repetition } : {}),
      condition: result.condition,
      integrity: result.integrityPassed ? 'yes' : 'NO',
      [rankedCandidates ? 'hit@3' : 'chunk hit']: Number.isFinite(result.chunkHit)
        ? (result.chunkHit === 1 ? 'yes' : 'NO')
        : 'n/a',
      ...(rankedCandidates ? {
        'hit@1': Number.isFinite(result.top1ChunkHit)
          ? (result.top1ChunkHit === 1 ? 'yes' : 'NO')
          : 'n/a',
        candidates: result.candidateCount,
      } : {}),
      expected: interval(result.expectedStart, result.expectedEnd),
      'top candidate': interval(result.predictedStart, result.predictedEnd),
      coverage: fixed(result.eventCoverage, 3),
      'duration valid': Number.isFinite(result.durationInRange)
        ? (result.durationInRange === 1 ? 'yes' : 'NO')
        : 'n/a',
      time: seconds(result.latencyMs),
    })));
    console.log('Per-run boundary diagnostics (secondary):');
    console.table(evaluation.results.map((result) => ({
      ...(tasks.size === 1 ? {} : { task: result.task }),
      condition: result.condition,
      'start Δ': signedSeconds(boundaryError(result.predictedStart, result.expectedStart)),
      'end Δ': signedSeconds(boundaryError(result.predictedEnd, result.expectedEnd)),
      'duration Δ': signedSeconds(durationError(result)),
      'top-1 IoU': fixed(result.iou, 4),
      ...(rankedCandidates ? { 'best@3 IoU': fixed(result.bestIou, 4) } : {}),
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
      requests: integer(result.requests),
      turns: result.modelTurns,
      items: result.agentItems,
      tools: result.toolCalls,
      MCP: result.mcpCalls,
      shell: result.shellCalls,
      skill: result.skillLoads,
      'Promptfoo cost': money(result.cost),
    })));
  } else {
    console.log(`Per-run table omitted for ${evaluation.results.length} runs; use results --all to print it.`);
  }

  const failures = evaluation.results.filter((result) => result.integrityPassed === false);
  if (failures.length > 0) {
    console.log('Condition-integrity exclusions:');
    for (const failure of failures) {
      console.log(
        `  ${failure.task} repetition ${failure.repetition} [${failure.condition}]: `
        + failure.integrityReason,
      );
    }
  }
  const unscorable = evaluation.results.filter((result) => (
    result.integrityPassed === true && !Number.isFinite(result.chunkHit)
  ));
  if (unscorable.length > 0) {
    console.log('Condition-valid but unscorable outputs:');
    for (const failure of unscorable) {
      console.log(
        `  ${failure.task} repetition ${failure.repetition} [${failure.condition}]: `
        + failure.qualityReason,
      );
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
      .filter((result) => (
        result.expectedVidxp
        && result.integrityPassed === true
        && traces[result.sourceJobId]
      ))
      .map((result) => summarizeRetrieval(result, traces[result.sourceJobId]));
    if (retrievals.length > 0) {
      console.log('VidXP MCP surfaced-target recall:');
      console.table(CONDITION_ORDER.filter((condition) => (
        retrievals.some((retrieval) => retrieval.condition === condition)
      )).map((condition) => {
        const selected = retrievals.filter((retrieval) => retrieval.condition === condition);
        const at1 = summarizeSurfaceRecall(selected, 1);
        const at3 = summarizeSurfaceRecall(selected, 3);
        return {
          condition,
          jobs: at3.scored,
          'hit@1': `${at1.hits}/${at1.scored}`,
          'hit@1 rate': fixed(at1.rate, 3),
          'hit@3': `${at3.hits}/${at3.scored}`,
          'hit@3 rate': fixed(at3.rate, 3),
          'coverage@3': fixed(at3.meanBestCoverage, 3),
        };
      }));
      const transfer = summarizeSurfaceTransfer(retrievals, 3);
      console.log(
        `  Top-three evidence to final answer: ${transfer.surfacedAndReturned} surfaced and returned; `
        + `${transfer.surfacedOnly} surfaced but not returned; ${transfer.returnedOnly} returned `
        + `without a top-three surfaced hit; ${transfer.neither} neither.`,
      );
      console.log(
        '  This VidXP-only diagnostic scores the ready evidence tiles actually exposed by '
        + 'get_job_evidence. A hit covers at least half of the event available to a 10s window; '
        + 'it measures retrieval availability and does not replace the cross-condition 8–12s '
        + 'final-answer gate.',
      );
    }
    console.log('VidXP retrieval boundaries:');
    console.table(retrievals.map((retrieval) => ({
      task: retrieval.task,
      condition: retrieval.condition,
      expected: interval(retrieval.expectedStart, retrieval.expectedEnd),
      'top fused': interval(retrieval.topMoment?.start, retrieval.topMoment?.end),
      'fused IoU': fixed(retrieval.topMomentIou, 4),
      modalities: Array.isArray(retrieval.topMoment?.modalities)
        ? retrieval.topMoment.modalities.join(', ')
        : 'n/a',
      hits: Array.isArray(retrieval.topMoment?.hits) ? retrieval.topMoment.hits.length : 0,
    })));
    console.log('VidXP fused retrieval recall:');
    console.table(CONDITION_ORDER.filter((condition) => (
      retrievals.some((retrieval) => retrieval.condition === condition)
    )).flatMap((condition) => {
      const selected = retrievals.filter((retrieval) => retrieval.condition === condition);
      return [0.3, 0.5, 0.7].map((threshold) => ({
        condition,
        threshold,
        'R@1': fixed(retrievalRecallAt(selected, 1, threshold), 3),
        'R@3': fixed(retrievalRecallAt(selected, 3, threshold), 3),
        'R@5': fixed(retrievalRecallAt(selected, 5, threshold), 3),
      }));
    }));
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
        'fused rank': hit.fusedRank,
        rank: hit.rank,
        interval: interval(hit.start, hit.end),
        IoU: fixed(hit.iou, 4),
      }))
    )));
    console.log(
      '  Saved jobs contain hits retained in final fused moments. The report cannot recover '
      + 'modality candidates outside candidate_top_k or the final fused output. Retrieval '
      + 'R@K therefore covers only the fused moments saved by each agent-requested top_k.',
    );
  }
}

export function printLatestReport(options = {}) {
  renderReport(loadLatestEvaluation({ rescore: options.rescore === true }), options);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === resolve(process.argv[1])) {
  try {
    printLatestReport({
      showAll: process.argv.includes('--all'),
      showResponses: process.argv.includes('--responses'),
      showRetrieval: !process.argv.includes('--no-retrieval'),
      rescore: process.argv.includes('--rescore'),
    });
  } catch (error) {
    console.error(`Could not report the latest evaluation: ${error.message}`);
    process.exitCode = 1;
  }
}
