import { existsSync, readFileSync, rmSync, statSync } from 'node:fs';
import { isAbsolute, join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  EVALUATION_PERMISSION_PROFILE,
  evaluationPermissionConfigs,
} from './condition-state.mjs';

const benchmarkRoot = resolve(fileURLToPath(new URL('..', import.meta.url)));
const repositoryRoot = resolve(benchmarkRoot, '..', '..');
const manifestPath = join(benchmarkRoot, 'tasks', 'longvale-part9-pilot.json');
const codexLauncher = join(
  benchmarkRoot,
  'node_modules',
  '@openai',
  'codex',
  'bin',
  'codex.js',
);

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
const vidxpOnCodexHome = requireDirectory('VIDXP_EVAL_VIDXP_ON_CODEX_HOME');
const vidxpOffCodexHome = requireDirectory('VIDXP_EVAL_VIDXP_OFF_CODEX_HOME');
const cleanUserCodexHome = requireDirectory('VIDXP_EVAL_CLEAN_USER_CODEX_HOME');
const workspace = requireDirectory('VIDXP_EVAL_WORKSPACE');
const vidxpOnWorkspace = requireDirectory('VIDXP_EVAL_VIDXP_ON_WORKSPACE');
const vidxpOffWorkspace = requireDirectory('VIDXP_EVAL_VIDXP_OFF_WORKSPACE');
const cleanUserWorkspace = requireDirectory('VIDXP_EVAL_CLEAN_USER_WORKSPACE');
requireDirectory('VIDXP_EVAL_DATA_DIR');
requireDirectory('VIDXP_EVAL_INDEX_DIR');
requireDirectory('VIDXP_MODEL_CACHE');
const uvCacheDirectory = requireDirectory('VIDXP_EVAL_UV_CACHE_DIR');
requireFile('VIDXP_MCP_COMMAND');
const promptfooPython = requireFile('PROMPTFOO_PYTHON');
if (!existsSync(codexLauncher)) {
  throw new Error(`The pinned Codex launcher is missing: ${codexLauncher}`);
}
const machineId = process.env.VIDXP_EVAL_MACHINE_ID;
if (!machineId || !/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(machineId)) {
  throw new Error(
    'VIDXP_EVAL_MACHINE_ID must be a stable repository machine ID; rerun setup with --machine-id.',
  );
}

if (!existsSync(join(codexHome, 'auth.json'))) {
  throw new Error('The isolated authentication home has no auth.json; run setup first.');
}

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

const permissionConfigs = evaluationPermissionConfigs(process.env);
for (const [condition, conditionHome] of Object.entries({
  vidxpOn: vidxpOnCodexHome,
  vidxpOff: vidxpOffCodexHome,
  cleanUser: cleanUserCodexHome,
})) {
  if (!existsSync(join(conditionHome, 'auth.json'))) {
    throw new Error(`Condition Codex home has no auth.json: ${conditionHome}`);
  }
  const codexConfig = join(conditionHome, 'config.toml');
  if (
    !existsSync(codexConfig)
    || readFileSync(codexConfig, 'utf8') !== permissionConfigs.configs[condition]
  ) {
    throw new Error(`Condition Codex isolation is missing or stale: ${conditionHome}`);
  }
}

const tasks = JSON.parse(readFileSync(manifestPath, 'utf8'));
const conditionWorkspaces = [vidxpOffWorkspace, cleanUserWorkspace];
const missingMedia = [...new Set([workspace, ...conditionWorkspaces]
  .flatMap((conditionWorkspace) => tasks
    .map((task) => join(conditionWorkspace, task.media_relpath)))
  .filter((path) => !existsSync(path)))];
if (missingMedia.length > 0) {
  throw new Error(`Pilot media is missing:\n${missingMedia.join('\n')}`);
}
for (const task of tasks) {
  const shared = statSync(join(workspace, task.media_relpath));
  const off = statSync(join(vidxpOffWorkspace, task.media_relpath));
  const cleanUser = statSync(join(cleanUserWorkspace, task.media_relpath));
  if (
    off.dev !== shared.dev
    || off.ino !== shared.ino
    || cleanUser.dev !== shared.dev
    || cleanUser.ino !== shared.ino
  ) {
    throw new Error(
      `Condition media is not hard-linked to the shared bytes: ${task.media_relpath}`,
    );
  }
  if (existsSync(join(vidxpOnWorkspace, task.media_relpath))) {
    throw new Error(
      `VidXP-on exposes source media that would permit a shell bypass: ${task.media_relpath}`,
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
const cleanUserSkillDirectory = join(
  cleanUserWorkspace,
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
if (existsSync(cleanUserSkillDirectory)) {
  throw new Error('The clean-user workspace must not contain the VidXP evidence skill.');
}
if (existsSync(sharedSkillDirectory)) {
  throw new Error('The shared parent workspace must not contain the VidXP evidence skill.');
}

const cleanPath = process.env.VIDXP_EVAL_CLEAN_USER_PATH;
if (!cleanPath) {
  throw new Error('VIDXP_EVAL_CLEAN_USER_PATH is required.');
}

if (process.platform === 'darwin') {
  function verifySandbox({ home, conditionWorkspace, allowedPath, ffmpegAllowed, path }) {
    const writeProbe = join(conditionWorkspace, 'tmp', '.vidxp-isolation-probe');
    rmSync(writeProbe, { force: true });
    const script = [
      'set -eu',
      'if /bin/cat "$VIDXP_PROBE_DENIED" >/dev/null 2>&1; then exit 41; fi',
      '/bin/cat "$VIDXP_PROBE_ALLOWED" >/dev/null',
      '/usr/bin/touch "$VIDXP_PROBE_WRITE"',
      ffmpegAllowed
        ? '"$VIDXP_PROBE_FFMPEG" -version >/dev/null 2>&1'
        : 'if "$VIDXP_PROBE_FFMPEG" -version >/dev/null 2>&1; then exit 42; fi',
    ].join('\n');
    const result = spawnSync(
      process.execPath,
      [
        codexLauncher,
        'sandbox',
        '--permission-profile',
        EVALUATION_PERMISSION_PROFILE,
        '--cd',
        conditionWorkspace,
        '/bin/zsh',
        '-c',
        script,
      ],
      {
        cwd: conditionWorkspace,
        env: {
          CODEX_HOME: home,
          HOME: conditionWorkspace,
          PATH: path,
          TMPDIR: join(conditionWorkspace, 'tmp'),
          VIDXP_PROBE_ALLOWED: allowedPath,
          VIDXP_PROBE_DENIED: join(repositoryRoot, 'README.md'),
          VIDXP_PROBE_FFMPEG: permissionConfigs.ffmpeg,
          VIDXP_PROBE_WRITE: writeProbe,
        },
        encoding: 'utf8',
        stdio: 'pipe',
      },
    );
    rmSync(writeProbe, { force: true });
    if (result.status !== 0) {
      throw new Error(
        `Codex did not enforce the ${EVALUATION_PERMISSION_PROFILE} profile in ${conditionWorkspace}:\n`
        + (result.stderr || result.stdout || result.error?.message),
      );
    }
  }

  const firstMediaPath = join(vidxpOffWorkspace, tasks[0].media_relpath);
  verifySandbox({
    home: vidxpOnCodexHome,
    conditionWorkspace: vidxpOnWorkspace,
    allowedPath: join(onSkillDirectory, 'SKILL.md'),
    ffmpegAllowed: false,
    path: process.env.PATH,
  });
  verifySandbox({
    home: vidxpOffCodexHome,
    conditionWorkspace: vidxpOffWorkspace,
    allowedPath: firstMediaPath,
    ffmpegAllowed: true,
    path: process.env.PATH,
  });
  verifySandbox({
    home: cleanUserCodexHome,
    conditionWorkspace: cleanUserWorkspace,
    allowedPath: join(cleanUserWorkspace, tasks[0].media_relpath),
    ffmpegAllowed: false,
    path: cleanPath,
  });
}

if (process.platform !== 'win32') {
  const cleanShell = spawnSync(
    '/bin/zsh',
    [
      '-lc',
      'for name in ffmpeg ffprobe vidxp vidxp-mcp; do '
        + 'if command -v "$name" >/dev/null 2>&1; then exit 42; fi; done; '
        + 'command -v curl >/dev/null',
    ],
    {
      cwd: cleanUserWorkspace,
      env: {
        HOME: cleanUserWorkspace,
        PATH: cleanPath,
        TMPDIR: join(cleanUserWorkspace, 'tmp'),
      },
      encoding: 'utf8',
      stdio: 'pipe',
    },
  );
  if (cleanShell.status !== 0) {
    throw new Error(
      cleanShell.status === 42
        ? 'The clean-user shell exposes a preinstalled media or VidXP executable.'
        : `The clean-user shell probe failed: ${cleanShell.stderr || cleanShell.error?.message}`,
    );
  }
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
      UV_CACHE_DIR: uvCacheDirectory,
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
  `Ready on ${machineId}: ${tasks.length} tasks across VidXP, direct-local, and clean-user conditions; no Codex or model inference calls made.\n`,
);
