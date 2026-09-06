import assert from 'node:assert/strict';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';

import {
  defaultEvaluationRoot,
  evaluationEnvironment,
  indexContainsPilot,
  libsqlBindingName,
  requireMachineId,
  savedMachineId,
  serializeEnvironment,
  versionAtLeast,
} from './setup-lib.mjs';
import { resetEvaluationWorkspace } from './reset-workspace.mjs';

test('checks the required Node version numerically', () => {
  assert.equal(versionAtLeast('22.21.9'), false);
  assert.equal(versionAtLeast('22.22.0'), true);
  assert.equal(versionAtLeast('23.0.0'), true);
});

test('recognizes a complete pilot index regardless of extra media', () => {
  const index = {
    items: [
      { original_filename: 'alpha.mp4' },
      { original_filename: 'beta.mp4' },
      { original_filename: 'unrelated.mp4' },
    ],
    modalities: ['scene', 'action', 'sound', 'speech'],
  };

  assert.equal(
    indexContainsPilot(index, ['alpha', 'beta'], ['scene', 'action', 'sound', 'speech']),
    true,
  );
  assert.equal(indexContainsPilot(index, ['alpha', 'missing'], ['scene']), false);
  assert.equal(indexContainsPilot(index, ['alpha'], ['scene', 'ocr']), false);
});

test('uses one optional root override for mutable setup state', () => {
  assert.equal(
    defaultEvaluationRoot({ VIDXP_EVAL_ROOT: 'C:/custom/eval' }, 'win32'),
    'C:\\custom\\eval',
  );
  assert.equal(
    defaultEvaluationRoot({ LOCALAPPDATA: 'C:/Users/test/AppData/Local' }, 'win32'),
    'C:\\Users\\test\\AppData\\Local\\VidXP\\benchmarks\\codex-mcp',
  );
  assert.equal(
    defaultEvaluationRoot({ XDG_DATA_HOME: '/tmp/data' }, 'linux'),
    '/tmp/data/vidxp/benchmarks/codex-mcp',
  );
});

test('selects the required Promptfoo SQLite binding for the host', () => {
  assert.equal(libsqlBindingName('win32', 'x64'), '@libsql/win32-x64-msvc');
  assert.equal(libsqlBindingName('darwin', 'arm64'), '@libsql/darwin-arm64');
  assert.equal(libsqlBindingName('linux', 'x64', '2.39'), '@libsql/linux-x64-gnu');
  assert.equal(libsqlBindingName('linux', 'x64'), '@libsql/linux-x64-musl');
  assert.throws(() => libsqlBindingName('win32', 'arm64'), /no pinned libsql binding/);
});

test('builds and serializes the environment consumed by Promptfoo', () => {
  const environment = evaluationEnvironment({
    benchmarkRoot: 'C:/repo/benchmarks/codex-mcp',
    repositoryRoot: 'C:/repo',
    evaluationRoot: 'C:/eval',
    indexSchemaVersion: 8,
    environment: {
      VIDXP_EVAL_MACHINE_ID: 'win-test-01',
      VIDXP_MODEL_CACHE: 'C:/shared-models',
    },
    platform: 'win32',
  });
  const serialized = serializeEnvironment(environment);

  assert.match(serialized, /VIDXP_EVAL_WORKSPACE="C:\/eval\/workspace"/);
  assert.match(serialized, /VIDXP_EVAL_MACHINE_ID="win-test-01"/);
  assert.match(serialized, /VIDXP_EVAL_INDEX_DIR="C:\/eval\/vidxp-index-schema-8"/);
  assert.match(serialized, /VIDXP_EVAL_VIDXP_ON_WORKSPACE="C:\/eval\/workspace\/vidxp-on"/);
  assert.match(serialized, /VIDXP_EVAL_VIDXP_OFF_WORKSPACE="C:\/eval\/workspace\/vidxp-off"/);
  assert.match(
    serialized,
    /VIDXP_EVAL_CLEAN_USER_WORKSPACE="C:\/eval\/workspace\/clean-user"/,
  );
  assert.match(serialized, /VIDXP_EVAL_VIDXP_ON_CODEX_HOME="C:\/eval\/codex-home\/vidxp-on"/);
  assert.match(serialized, /VIDXP_EVAL_VIDXP_OFF_CODEX_HOME="C:\/eval\/codex-home\/vidxp-off"/);
  assert.match(serialized, /VIDXP_EVAL_CLEAN_USER_CODEX_HOME="C:\/eval\/codex-home\/clean-user"/);
  assert.match(
    serialized,
    /VIDXP_EVAL_CLEAN_USER_PATH="C:\/Windows\/System32;C:\/Windows"/,
  );
  assert.match(serialized, /VIDXP_EVAL_UV_CACHE_DIR="C:\/eval\/uv-cache"/);
  assert.match(serialized, /VIDXP_MCP_COMMAND="C:\/repo\/\.venv\/Scripts\/vidxp-mcp\.exe"/);
  assert.match(serialized, /PROMPTFOO_PYTHON="C:\/repo\/\.venv\/Scripts\/python\.exe"/);
  assert.match(serialized, /VIDXP_EVAL_MODEL="gpt-5\.6-sol"/);
  assert.match(serialized, /VIDXP_MODEL_CACHE="C:\/shared-models"/);
  assert.doesNotMatch(serialized, /VIDXP_EVAL_ENV_FILE/);
  assert.doesNotMatch(serialized, /VIDXP_EVAL_ARTIFACT_DIR/);
});

test('always records the model cache used by the isolated runtime', () => {
  const environment = evaluationEnvironment({
    benchmarkRoot: '/repo/benchmarks/codex-mcp',
    repositoryRoot: '/repo',
    evaluationRoot: '/eval',
    indexSchemaVersion: 8,
    environment: { VIDXP_EVAL_MACHINE_ID: 'linux-test-01' },
    platform: 'linux',
  });

  assert.equal(environment.VIDXP_MODEL_CACHE, '/eval/vidxp-data/models');
});

test('requires and reloads a stable repository machine ID', () => {
  assert.equal(requireMachineId('mac-m2-01'), 'mac-m2-01');
  assert.throws(() => requireMachineId('MacBook Pro'), /machine ID/);

  const root = mkdtempSync(join(tmpdir(), 'vidxp-eval-machine-'));
  const envPath = join(root, '.env');
  writeFileSync(envPath, 'VIDXP_EVAL_MACHINE_ID="mac-m2-01"\n');
  assert.equal(savedMachineId(envPath), 'mac-m2-01');
  rmSync(root, { recursive: true, force: true });
});

test('resets clean-user state before every condition run', () => {
  const root = mkdtempSync(join(tmpdir(), 'vidxp-eval-reset-'));
  const workspaceRoot = join(root, 'workspace');
  const cleanWorkspace = join(workspaceRoot, 'clean-user');
  mkdirSync(join(cleanWorkspace, 'media'), { recursive: true });
  mkdirSync(join(cleanWorkspace, '.cache'), { recursive: true });
  writeFileSync(join(cleanWorkspace, '.cache', 'installed-tool'), 'stale');

  resetEvaluationWorkspace('clean-user', {
    VIDXP_EVAL_WORKSPACE: workspaceRoot,
    VIDXP_EVAL_CLEAN_USER_WORKSPACE: cleanWorkspace,
    VIDXP_EVAL_CLEAN_USER_PATH: '/usr/bin:/bin',
  });

  assert.equal(existsSync(join(cleanWorkspace, '.cache')), false);
  assert.equal(existsSync(join(cleanWorkspace, 'media')), true);
  assert.equal(existsSync(join(cleanWorkspace, 'tmp')), true);
  assert.equal(existsSync(join(cleanWorkspace, 'bin')), true);
  assert.equal(
    readFileSync(join(cleanWorkspace, '.zshenv'), 'utf8'),
    'export PATH="/usr/bin:/bin"\n',
  );
  rmSync(root, { recursive: true, force: true });
});
