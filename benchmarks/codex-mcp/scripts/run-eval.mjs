import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadLatestEvaluation, renderReport } from './report.mjs';
import { prepareConditionState } from './condition-state.mjs';

const benchmarkRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(benchmarkRoot, '..', '..');
const mode = process.argv[2];
if (!['smoke', 'pilot'].includes(mode)) {
  throw new Error('Evaluation mode must be smoke or pilot.');
}
const conditions = process.argv[3];
const requestedConditions = conditions?.split(',').filter(Boolean) || [];
const localSlmOnly = requestedConditions.length > 0 && requestedConditions.every(
  (condition) => condition === 'local-slm' || condition === 'local-slm-planner',
);
const evaluationEnvironment = {
  ...process.env,
  VIDXP_EVAL_MODE: mode,
  ...(conditions ? { VIDXP_EVAL_CONDITIONS: conditions } : {}),
};
if (!localSlmOnly) {
  prepareConditionState({ repositoryRoot, environment: evaluationEnvironment });
}

const preflightCommands = localSlmOnly
  ? [
    [
      evaluationEnvironment.PROMPTFOO_PYTHON,
      [join(benchmarkRoot, 'scripts', 'local_slm_provider.py'), '--check'],
    ],
    [
      evaluationEnvironment.PROMPTFOO_PYTHON,
      [join(benchmarkRoot, 'scripts', 'mcp_preflight.py')],
    ],
  ]
  : [[process.execPath, [join(benchmarkRoot, 'scripts', 'preflight.mjs')]]];
let preflightStatus = 0;
for (const [command, args] of preflightCommands) {
  const preflight = spawnSync(command, args, {
    cwd: repositoryRoot,
    env: evaluationEnvironment,
    stdio: 'inherit',
  });
  if (preflight.status !== 0) {
    if (preflight.error) {
      console.error(`Preflight could not start ${command}: ${preflight.error.message}`);
    }
    preflightStatus = preflight.status ?? 1;
    break;
  }
}
if (preflightStatus !== 0) {
  process.exitCode = preflightStatus;
} else {
  let previousEvaluationId = null;
  try {
    previousEvaluationId = loadLatestEvaluation().id;
  } catch {
    // A first evaluation has no prior result.
  }
  const evaluation = spawnSync(
    process.execPath,
    [
      join(benchmarkRoot, 'node_modules', 'promptfoo', 'dist', 'src', 'entrypoint.js'),
      'eval',
      '-c',
      'promptfooconfig.yaml',
      '--no-cache',
      '--no-share',
    ],
    { cwd: benchmarkRoot, env: evaluationEnvironment, stdio: 'inherit' },
  );
  let reportFailed = false;
  try {
    const latest = loadLatestEvaluation();
    if (latest.id === previousEvaluationId) {
      throw new Error('Promptfoo did not save a new evaluation.');
    }
    renderReport(latest);
  } catch (error) {
    console.error(`Could not report the completed evaluation: ${error.message}`);
    reportFailed = true;
  }
  process.exitCode = evaluation.status === 0 && !reportFailed
    ? 0
    : (evaluation.status || 1);
}
