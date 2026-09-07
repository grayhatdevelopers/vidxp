import {
  existsSync,
  mkdirSync,
  readdirSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { relative, resolve } from 'node:path';

const CONDITION_ENV = {
  'vidxp-on': 'VIDXP_EVAL_VIDXP_ON_WORKSPACE',
  'vidxp-off': 'VIDXP_EVAL_VIDXP_OFF_WORKSPACE',
  'clean-user': 'VIDXP_EVAL_CLEAN_USER_WORKSPACE',
};

function requireIsolatedWorkspace(condition, environment) {
  const sharedRoot = resolve(environment.VIDXP_EVAL_WORKSPACE || '');
  const workspace = resolve(environment[CONDITION_ENV[condition]] || '');
  const child = relative(sharedRoot, workspace);
  if (!child || child.startsWith('..') || resolve(sharedRoot, child) !== workspace) {
    throw new Error(`Refusing to reset non-isolated ${condition} workspace: ${workspace}`);
  }
  if (
    !existsSync(workspace)
    || (condition !== 'vidxp-on' && !existsSync(resolve(workspace, 'media')))
  ) {
    throw new Error(`The ${condition} workspace is not prepared: ${workspace}`);
  }
  return workspace;
}

export function resetEvaluationWorkspace(condition, environment = process.env) {
  if (condition === 'local-slm' || condition === 'local-slm-planner') {
    return;
  }
  if (!(condition in CONDITION_ENV)) {
    throw new Error(`Unknown evaluation condition: ${condition}`);
  }
  const workspace = requireIsolatedWorkspace(condition, environment);
  const preserved = new Set(condition === 'vidxp-on' ? ['.agents'] : ['media']);
  for (const entry of readdirSync(workspace)) {
    if (!preserved.has(entry)) {
      rmSync(resolve(workspace, entry), { recursive: true, force: true });
    }
  }
  mkdirSync(resolve(workspace, 'tmp'), { recursive: true });
  if (condition === 'clean-user') {
    mkdirSync(resolve(workspace, 'bin'), { recursive: true });
    const profile = `export PATH=${JSON.stringify(environment.VIDXP_EVAL_CLEAN_USER_PATH)}\n`;
    for (const filename of ['.zshenv', '.zprofile', '.profile']) {
      writeFileSync(resolve(workspace, filename), profile, 'utf8');
    }
  }
}

export async function beforeEach({ test }) {
  resetEvaluationWorkspace(test?.vars?.condition);
  return { test };
}
