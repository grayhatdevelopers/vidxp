import { spawnSync } from 'node:child_process';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { loadLatestEvaluation, renderReport } from './report.mjs';

const benchmarkRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const mode = process.argv[2];
const modeArguments = {
  smoke: ['--filter-first-n', '2', '--repeat', '1'],
  pilot: ['--filter-range', '2:', '--repeat', '3'],
};
if (!(mode in modeArguments)) {
  throw new Error('Evaluation mode must be smoke or pilot.');
}

const preflight = spawnSync(
  process.execPath,
  [join(benchmarkRoot, 'scripts', 'preflight.mjs')],
  { cwd: benchmarkRoot, env: process.env, stdio: 'inherit' },
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
      ...modeArguments[mode],
      '--no-cache',
      '--no-share',
    ],
    { cwd: benchmarkRoot, env: process.env, stdio: 'inherit' },
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
