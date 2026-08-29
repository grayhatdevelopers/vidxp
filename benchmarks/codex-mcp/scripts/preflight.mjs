import { existsSync, readFileSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const benchmarkRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
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
const dataDirectory = requireDirectory('VIDXP_EVAL_DATA_DIR');
const indexDirectory = requireDirectory('VIDXP_EVAL_INDEX_DIR');
const mcpCommand = requireFile('VIDXP_MCP_COMMAND');

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
const missingMedia = [...new Set(tasks
  .map((task) => join(workspace, task.media_relpath))
  .filter((path) => !existsSync(path)))];
if (missingMedia.length > 0) {
  throw new Error(`Pilot media is missing:\n${missingMedia.join('\n')}`);
}

const check = spawnSync(
  mcpCommand,
  [
    '--check',
    '--repository', process.env.VIDXP_EVAL_REPOSITORY || 'default',
    '--index-directory', indexDirectory,
    '--data-dir', dataDirectory,
    '--device', process.env.VIDXP_EVAL_DEVICE || 'cpu',
  ],
  { encoding: 'utf8', stdio: 'pipe' },
);
if (check.status !== 0) {
  throw new Error(`VidXP MCP preflight failed:\n${check.stderr || check.stdout}`);
}

process.stdout.write(check.stdout);
process.stdout.write(`Ready: ${tasks.length} tasks, 2 conditions, no model calls made.\n`);
