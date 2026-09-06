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
const evaluationEnvironment = {
  ...process.env,
  VIDXP_EVAL_MODE: mode,
};
prepareConditionState({ repositoryRoot, environment: evaluationEnvironment });

const preflight = spawnSync(
  process.execPath,
  [join(benchmarkRoot, 'scripts', 'preflight.mjs')],
  { cwd: benchmarkRoot, env: evaluationEnvironment, stdio: 'inherit' },
);
if (preflight.status !== 0) {
  process.exitCode = preflight.status ?? 1;
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
