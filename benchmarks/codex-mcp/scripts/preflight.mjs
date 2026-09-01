import { existsSync, readFileSync, statSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const benchmarkRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const repositoryRoot = resolve(benchmarkRoot, '..', '..');
const manifestPath = join(benchmarkRoot, 'tasks', 'longvale-part9-pilot.json');

const requiredNode = [22, 22, 0];
const currentNode = process.versions.node.split('.').map(Number);
const firstDifference = requiredNode.findIndex(
  (part, index) => currentNode[index] !== part,
);
const nodeIsSupported = firstDifference === -1
  || currentNode[firstDifference] > requiredNode[firstDifference];
if (!nodeIsSupported) {
  throw new Error(
    `Node.js 22.22.0 or newer is required; found ${process.versions.node}.`,
  );
}

function requireDirectory(name) {
  const value = process.env[name];
  if (!value || !isAbsolute(value) || !existsSync(value)) {
    throw new Error(`${name} must name an existing absolute directory.`);
  }
  return value;
}

function requireFile(name) {
  const value = process.env[name];
  if (!value || !isAbsolute(value) || !existsSync(value)) {
    throw new Error(`${name} must name an existing absolute file.`);
  }
  return value;
}

const codexHome = requireDirectory('VIDXP_EVAL_CODEX_HOME');
const workspace = requireDirectory('VIDXP_EVAL_WORKSPACE');
const vidxpOnWorkspace = requireDirectory('VIDXP_EVAL_VIDXP_ON_WORKSPACE');
const vidxpOffWorkspace = requireDirectory('VIDXP_EVAL_VIDXP_OFF_WORKSPACE');
requireDirectory('VIDXP_EVAL_DATA_DIR');
requireDirectory('VIDXP_EVAL_INDEX_DIR');
requireDirectory('VIDXP_MODEL_CACHE');
requireFile('VIDXP_MCP_COMMAND');
const promptfooPython = requireFile('PROMPTFOO_PYTHON');

const scorerRuntime = spawnSync(
  promptfooPython,
  [
    '-c',
    [
      'import vidxp.composition',
      'import vidxp.infrastructure.dbos_jobs',
      'import vidxp.workflow_runtime',
    ].join('; '),
  ],
  { cwd: repositoryRoot, encoding: 'utf8', stdio: 'pipe' },
);
if (scorerRuntime.status !== 0) {
  throw new Error(
    `Promptfoo scorer runtime cannot import VidXP:\n${scorerRuntime.stderr
      || scorerRuntime.stdout
      || scorerRuntime.error?.message}`,
  );
}

if (!existsSync(join(codexHome, 'auth.json'))) {
  throw new Error('The isolated Codex home has no auth.json; sign in there before evaluating.');
}

const codexConfig = join(codexHome, 'config.toml');
if (existsSync(codexConfig)) {
  const content = readFileSync(codexConfig, 'utf8');
  if (/^\s*\[mcp_servers(?:\.|\])/m.test(content)) {
    throw new Error('The isolated Codex home config contains ambient MCP servers.');
  }
}

const tasks = JSON.parse(readFileSync(manifestPath, 'utf8'));
const missingMedia = [...new Set([workspace, vidxpOnWorkspace, vidxpOffWorkspace]
  .flatMap((conditionWorkspace) => tasks
    .map((task) => join(conditionWorkspace, task.media_relpath)))
  .filter((path) => !existsSync(path)))];
if (missingMedia.length > 0) {
  throw new Error(`Pilot media is missing:\n${missingMedia.join('\n')}`);
}
for (const task of tasks) {
  const shared = statSync(join(workspace, task.media_relpath));
  const on = statSync(join(vidxpOnWorkspace, task.media_relpath));
  const off = statSync(join(vidxpOffWorkspace, task.media_relpath));
  if (
    on.dev !== shared.dev
    || on.ino !== shared.ino
    || off.dev !== shared.dev
    || off.ino !== shared.ino
  ) {
    throw new Error(
      `Condition media is not hard-linked to the shared bytes: ${task.media_relpath}`,
    );
  }
}

const sourceSkillDirectory = join(
  repositoryRoot,
  'plugins',
  'vidxp',
  'skills',
  'vidxp-find-video-evidence',
);
const onSkillDirectory = join(
  vidxpOnWorkspace,
  '.agents',
  'skills',
  'vidxp-find-video-evidence',
);
const offSkillDirectory = join(
  vidxpOffWorkspace,
  '.agents',
  'skills',
  'vidxp-find-video-evidence',
);
const sharedSkillDirectory = join(
  workspace,
  '.agents',
  'skills',
  'vidxp-find-video-evidence',
);
for (const relativePath of ['SKILL.md', join('agents', 'openai.yaml')]) {
  const source = join(sourceSkillDirectory, relativePath);
  const installed = join(onSkillDirectory, relativePath);
  const matchesCommittedSkill = existsSync(installed)
    && readFileSync(installed, 'utf8') === readFileSync(source, 'utf8');
  if (!matchesCommittedSkill) {
    throw new Error(
      `The VidXP-on workspace does not contain the committed ${relativePath}.`,
    );
  }
}
if (existsSync(offSkillDirectory)) {
  throw new Error('The VidXP-off workspace must not contain the VidXP evidence skill.');
}
if (existsSync(sharedSkillDirectory)) {
  throw new Error('The shared parent workspace must not contain the VidXP evidence skill.');
}

const check = spawnSync(
  process.platform === 'win32' ? 'uv.exe' : 'uv',
  [
    'run', '--no-sync', 'python',
    join(benchmarkRoot, 'scripts', 'mcp_preflight.py'),
  ],
  {
    cwd: repositoryRoot,
    env: {
      ...process.env,
      VIDXP_ALLOW_MODEL_DOWNLOADS: 'false',
    },
    encoding: 'utf8',
    stdio: 'pipe',
  },
);
if (check.status !== 0) {
  throw new Error(
    `VidXP MCP preflight failed:\n${check.stderr || check.stdout || check.error?.message}`,
  );
}

process.stdout.write(check.stdout);
process.stdout.write(
  `Ready: ${tasks.length} tasks, VidXP skill+MCP on versus VidXP off, no Codex or model inference calls made.\n`,
);
