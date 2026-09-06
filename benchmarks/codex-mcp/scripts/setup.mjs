import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { homedir } from 'node:os';
import {
  copyFileSync,
  createReadStream,
  existsSync,
  linkSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  REQUIRED_NODE_VERSION,
  defaultEvaluationRoot,
  evaluationEnvironment,
  indexContainsPilot,
  libsqlBindingName,
  requireMachineId,
  savedMachineId,
  serializeEnvironment,
  versionAtLeast,
} from './setup-lib.mjs';
import { prepareConditionState } from './condition-state.mjs';

const benchmarkRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = resolve(benchmarkRoot, '..', '..');
const manifestPath = join(benchmarkRoot, 'tasks', 'longvale-part9-pilot.json');
const datasetRevision = '18889b01886e30c36b0d1c650ac4439ad460ee73';
const archiveHash = 'c83d62557f102c6d41ea95c2c3b3581657481c8646cc70b1e12a85ead27a7ae3';
const archiveRelativePath = join('raw_videos_test', 'LongVALE_test_1171_part_9.zip');
const annotationFilename = 'longvale-annotations-eval.json';
const modalities = ['scene', 'action', 'sound', 'speech'];
function executableName(command) {
  return process.platform === 'win32' && command === 'npm' ? 'npm.cmd' : command;
}

function installedDesktopModelCache() {
  const candidates = [];
  if (process.platform === 'darwin') {
    candidates.push(join(homedir(), 'Library', 'Application Support', 'VidXP', 'models'));
  } else if (process.platform === 'win32' && process.env.LOCALAPPDATA) {
    candidates.push(join(process.env.LOCALAPPDATA, 'VidXP', 'models'));
  } else if (process.platform === 'linux') {
    candidates.push(join(
      process.env.XDG_DATA_HOME || join(homedir(), '.local', 'share'),
      'VidXP',
      'models',
    ));
  }
  return candidates.find((candidate) => existsSync(candidate));
}

function formatCommand(command, args) {
  return [command, ...args]
    .map((part) => (/\s/.test(part) ? JSON.stringify(part) : part))
    .join(' ');
}

function run(command, args, { cwd = repositoryRoot, env = process.env, capture = false } = {}) {
  process.stdout.write(`\n> ${formatCommand(command, args)}\n`);
  const result = spawnSync(executableName(command), args, {
    cwd,
    env,
    encoding: capture ? 'utf8' : undefined,
    stdio: capture ? 'pipe' : 'inherit',
  });
  if (result.error) {
    throw new Error(`Could not run ${command}: ${result.error.message}`);
  }
  if (result.status !== 0) {
    const detail = capture ? `\n${result.stderr || result.stdout}` : '';
    throw new Error(`${command} exited with status ${result.status}.${detail}`);
  }
  return capture ? result.stdout : '';
}

async function sha256(path) {
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(path)) {
    hash.update(chunk);
  }
  return hash.digest('hex');
}

function readIndex(environment) {
  try {
    const output = run(
      'uv',
      [
        'run', '--no-sync', 'vidxp',
        '--data-dir', environment.VIDXP_EVAL_DATA_DIR,
        '--index-dir', environment.VIDXP_EVAL_INDEX_DIR,
        'index', 'list', '--json',
      ],
      { env: { ...process.env, ...environment }, capture: true },
    );
    return JSON.parse(output);
  } catch {
    return null;
  }
}

async function main() {
  if (!versionAtLeast(process.versions.node)) {
    throw new Error(
      `Node.js ${REQUIRED_NODE_VERSION.join('.')} or newer is required; found ${process.versions.node}.`,
    );
  }

  run('uv', ['--version'], { capture: true });

  const argumentsList = process.argv.slice(2);
  let requestedMachineId = null;
  if (argumentsList.length > 0) {
    if (argumentsList.length !== 2 || argumentsList[0] !== '--machine-id') {
      throw new Error('Usage: setup --machine-id <repository-machine-id>');
    }
    requestedMachineId = argumentsList[1];
  }

  const evaluationRoot = defaultEvaluationRoot(process.env);
  const uvCacheDirectory = join(evaluationRoot, 'uv-cache');
  mkdirSync(uvCacheDirectory, { recursive: true });
  const uvEnvironment = { ...process.env, UV_CACHE_DIR: uvCacheDirectory };
  const desktopModelCache = installedDesktopModelCache();
  const machineId = requireMachineId(
    requestedMachineId
      || process.env.VIDXP_EVAL_MACHINE_ID
      || savedMachineId(join(benchmarkRoot, '.env')),
  );
  const setupSourceEnvironment = {
    ...process.env,
    VIDXP_EVAL_MACHINE_ID: machineId,
    ...(process.env.VIDXP_MODEL_CACHE || !desktopModelCache
      ? {}
      : { VIDXP_MODEL_CACHE: desktopModelCache }),
  };
  run(
    'uv',
    [
      'sync', '--frozen', '--extra', 'local-worker', '--extra', 'mcp', '--extra', 'server',
      '--extra', 'benchmarks', '--extra', 'test',
    ],
    { env: uvEnvironment },
  );
  const indexSchemaVersion = Number(run(
    'uv',
    [
      'run', '--no-sync', 'python', '-c',
      'from vidxp.core.contracts import INDEX_SCHEMA_VERSION; print(INDEX_SCHEMA_VERSION)',
    ],
    { capture: true, env: uvEnvironment },
  ).trim());
  const setupEnvironment = evaluationEnvironment({
    benchmarkRoot,
    repositoryRoot,
    evaluationRoot,
    indexSchemaVersion,
    environment: setupSourceEnvironment,
  });
  const commandEnvironment = { ...process.env, ...setupEnvironment };
  const tasks = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const videoIds = [...new Set(tasks.map((task) => task.video_id))];

  run(
    'uv',
    ['run', '--no-sync', 'vidxp', 'init', '--yes'],
    { env: commandEnvironment },
  );
  run('npm', ['ci'], { cwd: benchmarkRoot });

  const glibcVersion = process.report?.getReport().header.glibcVersionRuntime;
  const bindingName = libsqlBindingName(process.platform, process.arch, glibcVersion);
  const libsqlManifest = JSON.parse(readFileSync(
    join(benchmarkRoot, 'node_modules', 'libsql', 'package.json'),
    'utf8',
  ));
  const bindingVersion = libsqlManifest.optionalDependencies?.[bindingName];
  if (!bindingVersion) {
    throw new Error(`The Promptfoo lock does not declare ${bindingName}.`);
  }
  const codexManifest = JSON.parse(readFileSync(
    join(benchmarkRoot, 'node_modules', '@openai', 'codex', 'package.json'),
    'utf8',
  ));
  const codexBindingName = `@openai/codex-${process.platform}-${process.arch}`;
  const codexBindingVersion = codexManifest.optionalDependencies?.[codexBindingName];
  if (!codexBindingVersion) {
    throw new Error(`The pinned Codex package does not support ${process.platform}-${process.arch}.`);
  }
  run(
    'npm',
    [
      'install', '--no-save', '--package-lock=false',
      `${bindingName}@${bindingVersion}`,
      `${codexBindingName}@${codexBindingVersion}`,
    ],
    { cwd: benchmarkRoot },
  );
  run('codex', ['--version'], { capture: true });
  run(
    process.execPath,
    [
      join(benchmarkRoot, 'node_modules', 'promptfoo', 'dist', 'src', 'entrypoint.js'),
      'validate', '-c', join(benchmarkRoot, 'promptfooconfig.yaml'),
    ],
    { cwd: benchmarkRoot, env: commandEnvironment },
  );

  for (const directory of [
    setupEnvironment.VIDXP_EVAL_CODEX_HOME,
    setupEnvironment.VIDXP_EVAL_VIDXP_ON_CODEX_HOME,
    setupEnvironment.VIDXP_EVAL_VIDXP_OFF_CODEX_HOME,
    setupEnvironment.VIDXP_EVAL_CLEAN_USER_CODEX_HOME,
    setupEnvironment.VIDXP_EVAL_UV_CACHE_DIR,
    setupEnvironment.VIDXP_EVAL_WORKSPACE,
    join(setupEnvironment.VIDXP_EVAL_WORKSPACE, 'media'),
    setupEnvironment.VIDXP_EVAL_VIDXP_ON_WORKSPACE,
    setupEnvironment.VIDXP_EVAL_VIDXP_OFF_WORKSPACE,
    join(setupEnvironment.VIDXP_EVAL_VIDXP_OFF_WORKSPACE, 'media'),
    setupEnvironment.VIDXP_EVAL_CLEAN_USER_WORKSPACE,
    join(setupEnvironment.VIDXP_EVAL_CLEAN_USER_WORKSPACE, 'media'),
    join(setupEnvironment.VIDXP_EVAL_CLEAN_USER_WORKSPACE, 'bin'),
    join(setupEnvironment.VIDXP_EVAL_CLEAN_USER_WORKSPACE, 'tmp'),
    setupEnvironment.VIDXP_EVAL_DATA_DIR,
    setupEnvironment.VIDXP_EVAL_INDEX_DIR,
    setupEnvironment.VIDXP_EVAL_ARTIFACT_DIR,
  ]) {
    mkdirSync(directory, { recursive: true });
  }
  rmSync(
    join(setupEnvironment.VIDXP_EVAL_VIDXP_ON_WORKSPACE, 'media'),
    { recursive: true, force: true },
  );
  prepareConditionState({ repositoryRoot, environment: commandEnvironment });
  if (!existsSync(setupEnvironment.VIDXP_MCP_COMMAND)) {
    throw new Error(`VidXP MCP executable was not created at ${setupEnvironment.VIDXP_MCP_COMMAND}.`);
  }
  if (process.platform !== 'win32') {
    const cleanPathProfile = `export PATH=${JSON.stringify(
      setupEnvironment.VIDXP_EVAL_CLEAN_USER_PATH,
    )}\n`;
    for (const profile of ['.zshenv', '.zprofile', '.profile']) {
      writeFileSync(
        join(setupEnvironment.VIDXP_EVAL_CLEAN_USER_WORKSPACE, profile),
        cleanPathProfile,
        'utf8',
      );
    }
  }
  writeFileSync(
    setupEnvironment.VIDXP_EVAL_ENV_FILE,
    serializeEnvironment(setupEnvironment),
    'utf8',
  );

  const authPath = join(setupEnvironment.VIDXP_EVAL_CODEX_HOME, 'auth.json');
  if (!existsSync(authPath)) {
    process.stdout.write('\nSign in to the isolated Codex profile when prompted.\n');
    run('codex', ['login'], {
      env: { ...commandEnvironment, CODEX_HOME: setupEnvironment.VIDXP_EVAL_CODEX_HOME },
    });
  }
  if (!existsSync(authPath)) {
    throw new Error('Codex login completed without creating auth.json in the isolated profile.');
  }
  for (const conditionHome of [
    setupEnvironment.VIDXP_EVAL_VIDXP_ON_CODEX_HOME,
    setupEnvironment.VIDXP_EVAL_VIDXP_OFF_CODEX_HOME,
    setupEnvironment.VIDXP_EVAL_CLEAN_USER_CODEX_HOME,
  ]) {
    copyFileSync(authPath, join(conditionHome, 'auth.json'));
  }

  process.stdout.write(
    '\nDownloading the pinned LongVALE pilot files. Use of the dataset is subject to its published terms.\n',
  );
  run(
    'uvx',
    [
      'hf', 'download', 'ttgeng233/LongVALE',
      annotationFilename,
      archiveRelativePath.replaceAll('\\', '/'),
      '--repo-type', 'dataset',
      '--revision', datasetRevision,
      '--local-dir', setupEnvironment.VIDXP_EVAL_ARTIFACT_DIR,
    ],
    { env: commandEnvironment },
  );

  const archivePath = join(setupEnvironment.VIDXP_EVAL_ARTIFACT_DIR, archiveRelativePath);
  const actualHash = await sha256(archivePath);
  if (actualHash !== archiveHash) {
    throw new Error(`LongVALE archive hash mismatch: expected ${archiveHash}, found ${actualHash}.`);
  }

  const sourceMedia = join(setupEnvironment.VIDXP_EVAL_ARTIFACT_DIR, 'video_test_1171');
  if (videoIds.some((videoId) => !existsSync(join(sourceMedia, `${videoId}.mp4`)))) {
    run(
      'uv',
      ['run', '--no-sync', 'python', '-m', 'zipfile', '-e', archivePath, setupEnvironment.VIDXP_EVAL_ARTIFACT_DIR],
      { env: commandEnvironment },
    );
  }
  for (const videoId of videoIds) {
    const source = join(sourceMedia, `${videoId}.mp4`);
    if (!existsSync(source)) {
      throw new Error(`The LongVALE archive did not contain ${source}.`);
    }
    const sharedMedia = join(setupEnvironment.VIDXP_EVAL_WORKSPACE, 'media', `${videoId}.mp4`);
    copyFileSync(source, sharedMedia);
    for (const conditionWorkspace of [
      setupEnvironment.VIDXP_EVAL_VIDXP_OFF_WORKSPACE,
      setupEnvironment.VIDXP_EVAL_CLEAN_USER_WORKSPACE,
    ]) {
      const conditionMedia = join(conditionWorkspace, 'media', `${videoId}.mp4`);
      if (!existsSync(conditionMedia)) {
        linkSync(sharedMedia, conditionMedia);
      }
    }
  }

  run(
    'uv',
    [
      'run', '--no-sync', 'vidxp',
      '--data-dir', setupEnvironment.VIDXP_EVAL_DATA_DIR,
      '--index-dir', setupEnvironment.VIDXP_EVAL_INDEX_DIR,
      'jobs', 'stop-worker',
    ],
    { env: commandEnvironment },
  );

  run(
    'uv',
    [
      'run', '--no-sync', 'vidxp',
      '--data-dir', setupEnvironment.VIDXP_EVAL_DATA_DIR,
      '--index-dir', setupEnvironment.VIDXP_EVAL_INDEX_DIR,
      'prepare', '--modalities', modalities.join(','), '--yes',
    ],
    { env: commandEnvironment },
  );

  const currentIndex = readIndex(setupEnvironment);
  if (!indexContainsPilot(currentIndex, videoIds, modalities)) {
    for (const videoId of videoIds) {
      if (indexContainsPilot(currentIndex, [videoId], modalities)) {
        process.stdout.write(`\n${videoId}.mp4 is already indexed; skipping.\n`);
        continue;
      }
      process.stdout.write(`\nIndexing ${videoId}.mp4\n`);
      const mediaPath = join(setupEnvironment.VIDXP_EVAL_WORKSPACE, 'media', `${videoId}.mp4`);
      const imported = JSON.parse(run(
        'uv',
        [
          'run', '--no-sync', 'vidxp',
          '--data-dir', setupEnvironment.VIDXP_EVAL_DATA_DIR,
          '--index-dir', setupEnvironment.VIDXP_EVAL_INDEX_DIR,
          'media', 'import', mediaPath, '--json',
        ],
        { env: commandEnvironment, capture: true },
      ));
      run(
        'uv',
        [
          'run', '--no-sync', 'vidxp',
          '--data-dir', setupEnvironment.VIDXP_EVAL_DATA_DIR,
          '--index-dir', setupEnvironment.VIDXP_EVAL_INDEX_DIR,
          'index', 'create', imported.media_id,
          ...modalities.flatMap((modality) => ['--modality', modality]),
        ],
        { env: commandEnvironment },
      );
    }
  } else {
    process.stdout.write('\nThe five pilot videos are already indexed; skipping indexing.\n');
  }

  run(
    process.execPath,
    [join(benchmarkRoot, 'scripts', 'preflight.mjs')],
    { cwd: benchmarkRoot, env: commandEnvironment },
  );

  process.stdout.write(
    '\nSetup complete. Run:\n'
      + '  ./benchmarks/codex-mcp/run smoke\n'
      + '  ./benchmarks/codex-mcp/run pilot\n'
      + '  ./benchmarks/codex-mcp/run view\n',
  );
}

main().catch((error) => {
  process.stderr.write(`\nSetup failed: ${error.message}\n`);
  process.exitCode = 1;
});
